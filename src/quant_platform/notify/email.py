"""SMTP 邮件发送器。

使用 Python 标准库 smtplib，支持纯文本 + HTML。
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from ..utils.exceptions import QuantPlatformError
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SmtpConfig:
    host: str
    port: int = 465
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    from_addr: str = ""
    timeout: int = 15


class SmtpClient:
    def __init__(self, config: SmtpConfig) -> None:
        self.config = config
        if not config.from_addr and config.username:
            self.config.from_addr = config.username

    def send(
        self,
        subject: str,
        body_text: str,
        to_addrs: List[str],
        body_html: Optional[str] = None,
    ) -> bool:
        if not self.config.host or not to_addrs:
            logger.warning("SMTP 未配置或无收件人，跳过发送")
            return False
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config.from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            if self.config.use_ssl:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    self.config.host, self.config.port,
                    context=ctx, timeout=self.config.timeout,
                ) as server:
                    if self.config.username:
                        server.login(self.config.username, self.config.password)
                    server.sendmail(self.config.from_addr, to_addrs, msg.as_string())
            else:
                with smtplib.SMTP(
                    self.config.host, self.config.port,
                    timeout=self.config.timeout,
                ) as server:
                    server.starttls()
                    if self.config.username:
                        server.login(self.config.username, self.config.password)
                    server.sendmail(self.config.from_addr, to_addrs, msg.as_string())
            logger.info("邮件已发送: %s -> %s", subject, to_addrs)
            return True
        except Exception as e:
            raise QuantPlatformError(f"邮件发送失败: {e}") from e
