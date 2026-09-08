from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositoryBase(BaseModel):
    github_id: str
    name: str
    full_name: str
    owner: str
    url: str
    default_branch: str = "main"


class RepositoryCreate(RepositoryBase):
    pass


class RepositoryResponse(RepositoryBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

