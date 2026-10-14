"""Validated settings shared by the API and isolated workers."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(default="", max_length=20000)
    clash_secret: str = Field(default="", max_length=1000)
    proxy_url: str = "http://127.0.0.1:7890"
    clash_transport: Literal["unix", "http"] = "unix"
    clash_socket: str = Field(default="/tmp/verge/verge-mihomo.sock", max_length=1000)
    clash_url: str = "http://127.0.0.1:9090"
    rule_group: str = Field(default="🤖 AI", min_length=1, max_length=200)
    global_group: str = Field(default="GLOBAL", min_length=1, max_length=200)
    auto_switch: bool = True
    request_delay: float = Field(default=0.8, ge=0.15, le=60)
    request_timeout: float = Field(default=20, ge=5, le=120)
    default_mode: Literal["scan", "discover", "matrix"] = "discover"
    default_region: str = "GB"
    collect_price: bool = True
    use_known_bases: bool = True
    include_company_lists: bool = False
    custom_codes: list[str] = Field(default_factory=list, max_length=5000)
    custom_bases: list[str] = Field(default_factory=list, max_length=500)
    region_keywords: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("proxy_url", "clash_url")
    @classmethod
    def validate_url(cls, value, info):
        value = value.strip().rstrip("/")
        if not value and info.field_name == "proxy_url":
            return value
        parsed = urlsplit(value)
        allowed = {"http", "https"}
        if info.field_name == "proxy_url":
            allowed |= {"socks5", "socks5h"}
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if parsed.scheme not in allowed or not parsed.hostname or not valid_port:
            raise ValueError("请输入有效的代理或控制器地址（含协议和端口）")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("地址不支持内嵌凭据、查询参数或片段")
        if parsed.path not in ("", "/"):
            raise ValueError("请输入服务的根地址，不要附加路径")
        return value

    @field_validator("custom_codes", "custom_bases")
    @classmethod
    def validate_candidates(cls, values):
        cleaned = []
        for value in values:
            value = value.strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", value):
                raise ValueError("候选码和公司基础名仅支持字母、数字、下划线和连字符")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned

    @field_validator("region_keywords")
    @classmethod
    def validate_keywords(cls, values):
        if len(values) > 40:
            raise ValueError("地区关键字过多")
        for region, keywords in values.items():
            if not re.fullmatch(r"[A-Z]{2}", region):
                raise ValueError("地区代码必须为两位大写字母")
            if len(keywords) > 20 or any(not x.strip() or len(x) > 100 for x in keywords):
                raise ValueError("节点关键字不能为空，且每个地区最多 20 个")
        return values


class SettingsUpdate(Settings):
    # An omitted/blank credential preserves the current value, an explicit clear removes it.
    clear_token: bool = False
    clear_clash_secret: bool = False


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["scan", "discover", "matrix"]
    region: str = Field(default="GB", pattern=r"^(ALL|[A-Z]{2})$")
    collect_price: bool = True
    preview: bool = False


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "stop"]
