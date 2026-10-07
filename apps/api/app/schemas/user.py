from pydantic import BaseModel, EmailStr, Field

class UserBase(BaseModel):
    email: EmailStr
    name: str | None = None

class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=1024, repr=False)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024, repr=False)

class UserResponse(UserBase):
    id: int

    model_config = {"from_attributes": True}
