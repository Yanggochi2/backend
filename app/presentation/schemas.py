"""요청 본문 검증. JSON 필드는 camelCase, 파이썬 속성은 snake_case"""
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, field_validator
from pydantic.alias_generators import to_camel


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Body(BaseModel):
    # 정의하지 않은 필드(role, wardId 등)는 무시한다 (AUTH-01 보안: 역할은 요청 본문에서 받지 않음)
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Signup(Body):
    name: Name
    email: EmailStr
    password: str
    terms_agreed: bool

    @field_validator("password")
    @classmethod
    def password_rule(cls, v: str) -> str:
        if len(v) < 8 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("WEAK_PASSWORD")
        return v

    @field_validator("terms_agreed")
    @classmethod
    def must_agree(cls, v: bool) -> bool:
        if not v:
            raise ValueError("TERMS_NOT_AGREED")
        return v


class Login(Body):
    email: NonBlank
    password: NonBlank
