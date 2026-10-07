from app.agent.tools.sql_tools import validate_sql_query


def test_valid_select_query_passes():
    result = validate_sql_query.invoke("SELECT * FROM employees")
    assert result == "SELECT * FROM employees"


def test_strips_markdown_code_fences():
    result = validate_sql_query.invoke("```sql\nSELECT * FROM employees\n```")
    assert result == "SELECT * FROM employees"


def test_trailing_semicolon_is_tolerated():
    result = validate_sql_query.invoke("SELECT * FROM employees;")
    assert result == "SELECT * FROM employees"


def test_rejects_non_select_statement():
    result = validate_sql_query.invoke("UPDATE employees SET salary = 0")
    assert result.startswith("Error:")


def test_rejects_delete_keyword_anywhere_in_query():
    result = validate_sql_query.invoke("SELECT * FROM employees; DELETE FROM employees")
    assert result.startswith("Error:") and "DELETE" in result


def test_rejects_drop_table():
    result = validate_sql_query.invoke("DROP TABLE employees")
    assert result.startswith("Error:")