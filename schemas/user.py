from pydantic import BaseModel

class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "investigator"

class UserLogin(BaseModel):
    username: str
    password: str