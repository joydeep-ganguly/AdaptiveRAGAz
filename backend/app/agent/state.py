from pydantic import BaseModel,Field
from typing_extensions import TypedDict
from typing import Annotated
import operator

# =============================================================================
# Pydantic Schemas for Structured Outputs
# =============================================================================
class RouterQuery(BaseModel):
    datasource: str = Field(description="Datasource to route query to: 'retrieve' for financial documents, 'sql_agent' for employee information, 'web_search' for general knowledge, 'code_execution for data analysis and chart creation")


class AgentState(TypedDict):

    messages: Annotated[list, operator.add]
    retrieved_docs: str
    rewritten_queries: list[str]
