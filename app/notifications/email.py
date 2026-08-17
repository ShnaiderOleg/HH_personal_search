import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import Settings
from ..models import Vacancy
from ..services.vacancy_service import salary_label

logger = logging.getLogger(__name__)


def send_digest_email(settings: Settings, vacancies: list[Vacancy]) -> int:
    """Отправляет дайджест новых вакансий по SMTP. Возвращает число отправленных."""
    if not settings.smtp_host or not settings.email_to or not vacancies:
        return 0
    lines = [
        f"- {v.title} | {salary_label(v)} | {v.employer or '-'} | {v.url}"
        for v in vacancies
    ]
    body = "Новые вакансии:\n\n" + "\n".join(lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"HHSearch: {len(vacancies)} новых вакансий"
    msg["From"] = settings.email_from or settings.smtp_user
    msg["To"] = settings.email_to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(msg["From"], [settings.email_to], msg.as_string())
        return 1
    except smtplib.SMTPException as exc:
        logger.warning("email send failed: %s", exc)
        return 0
