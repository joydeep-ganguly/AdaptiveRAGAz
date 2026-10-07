import re

from langchain_core.tools import tool
from langchain_community.utilities import SQLDatabase

from app.agent.llm import get_sql_llm
from app.config import get_settings

llm = get_sql_llm()


def get_db_connection():
    settings = get_settings()
    db = SQLDatabase.from_uri(f"sqlite:///{settings.resolved_employees_db_path}")
    return db


db = get_db_connection()
tables = db.get_usable_table_names()
SCHEMA = db.get_table_info()


@tool
def get_database_schema(table_name: str = None):
    """Get database schema information for SQL query generation.
    Use this first to understand table structure before creating queries."""

    db = get_db_connection()

    if table_name:
        tables = db.get_usable_table_names()
        if table_name.lower() in [t.lower() for t in tables]:
            return db.get_table_info([table_name])
        else:
            return f"Error: Table '{table_name}' not found. Available tables: '{', '.join(tables)}'"
    else:
        return SCHEMA


@tool
def generate_sql_query(question: str, schema_info: str = None):
    """Generate a SQL SELECT query from a natural language question using database schema.
        Always use this after getting schema information."""

    schema_to_use = schema_info if schema_info else SCHEMA

    prompt = f"""Based on this database schema:
                {schema_to_use}

                Generate a SQL query to answer this question: {question}

                Rules:
                - Use only SELECT statements
                - Include only existing columns and tables
                - Add appropriate WHERE, GROUP BY, ORDER BY clauses as needed
                - Limit results to 10 rows unless specified otherwise
                - Use proper SQL syntax for SQLite

                Return only the SQL query, nothing else."""

    response = llm.invoke(prompt)
    sql_query = response.content[0]['text']
    print(f"[TOOL] generated Sql Query: {sql_query[:30]}...")
    return sql_query


@tool
def validate_sql_query(query: str):
    """Validate SQL query for safety and syntax before execution.
        Returns 'Valid: <query>' if safe or 'Error: <message>' if unsafe."""

    clean_query = query.strip()
    clean_query = re.sub(r'```sql\s*', '', clean_query, flags=re.IGNORECASE)
    clean_query = re.sub(r'```\s*', '', clean_query, flags=re.IGNORECASE)
    clean_query = clean_query.strip().rstrip(';')

    if not clean_query.lower().startswith('select'):
        return "Error: only select statements are allowed."

    dangerous_keywords = ['INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'CREATE', 'TRUNCATE']
    query_upper = clean_query.upper()

    for keyword in dangerous_keywords:
        if keyword in query_upper:
            return f"Error: {keyword} operations are not allowed"

    print("[TOOL]: Your sql query is validated. Passed!")
    return clean_query


@tool
def execute_sql_query(sql_query: str):
    """Execute a validated SQL query and return results.
    Only use this after validating the query for safety."""

    db = get_db_connection()

    query = validate_sql_query.invoke(sql_query)
    if query.startswith('Error:'):
        return f"Query '{sql_query}' validation failed with Error {query}"

    result = db.run(query)

    if result:
        return f"Query results: {result}"
    else:
        return f"Query executed successfully but no result was found!"


@tool
def fix_sql_error(original_query: str, error_message: str, question) -> dict:
    """Fix a failed SQL query by analyzing the error and generating a corrected version.
        Use this when validation or execution fails."""

    fix_prompt = f"""The following SQL query failed:
                Query: {original_query}
                Error: {error_message}
                Original Question: {question}

                Database Schema:
                {SCHEMA}

                Analyze the error and provide a corrected SQL query that:
                1. Fixes the specific error mentioned
                2. Still answers the original question
                3. Uses only valid table and column names from the schema
                4. Follows SQLite syntax rules

                Return only the corrected SQL query, nothing else."""

    response = llm.invoke(fix_prompt)
    query = response.content[0]['text'].strip()

    print(f"[TOOL] generated fixed sql query")

    return query


ALL_SQL_TOOLS = [
    get_database_schema,
    generate_sql_query,
    execute_sql_query,
    fix_sql_error,
]