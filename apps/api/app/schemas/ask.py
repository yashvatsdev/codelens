from pydantic import BaseModel, Field

class AskRequest(BaseModel):
    question: str = Field(..., max_length=2000)

class SourceReference(BaseModel):
    file_path: str
    start_line: int
    end_line: int

class AskResponse(BaseModel):
    answer: str
    sources: list[SourceReference] = Field(default_factory=list)
