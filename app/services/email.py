import logging
import httpx
from backend.app.core.config import settings

logger = logging.getLogger("estateflow.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"


async def send_resend_email(to_email: str, subject: str, html_content: str) -> dict:
    """Dispatches a real transactional email via Resend API."""
    if not settings.RESEND_API_KEY:
        error_msg = "Resend API key is not configured in server environment."
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(RESEND_ENDPOINT, json=payload, headers=headers)
            if response.status_code not in [200, 201]:
                logger.error(f"Resend delivery failed: {response.status_code} - {response.text}")
                raise RuntimeError(f"Email delivery provider error: {response.text}")

            result = response.json()
            logger.info(f"Email successfully delivered via Resend. Message ID: {result.get('id')}")
            return result
        except httpx.RequestError as exc:
            logger.error(f"Network error while calling Resend: {exc}")
            raise RuntimeError(f"Email delivery network error: {exc}")


async def send_invitation_email(to_email: str, recipient_name: str, role: str, activation_token: str) -> dict:
    """Delivers account activation email with single-use expiring token link."""
    activation_url = f"{settings.WEB_URL}/activate?token={activation_token}&email={to_email}"
    subject = f"Invitation to join G² Brothers CRM as {role.capitalize()}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F7F6F8; color: #292A3A; padding: 24px; }}
            .card {{ background-color: #FFFFFF; border-radius: 12px; border: 1px solid #E5E3EA; padding: 32px; max-width: 520px; margin: 0 auto; }}
            .brand {{ font-size: 20px; font-weight: 800; color: #292A3A; margin-bottom: 20px; }}
            .brand span {{ color: #72759A; }}
            .btn {{ display: inline-block; background-color: #72759A; color: #FFFFFF; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: 600; margin: 20px 0; }}
            .footer {{ font-size: 12px; color: #737487; margin-top: 24px; border-top: 1px solid #E5E3EA; padding-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="brand">estate<span>flow</span> <span style="font-size: 11px; background: #E8E7F0; color: #4F5278; padding: 2px 6px; border-radius: 4px; font-weight: 700;">CRM</span></div>
            <h2>Welcome, {recipient_name}!</h2>
            <p>You have been invited to join the private company real estate CRM as <strong>{role.capitalize()}</strong>.</p>
            <p>Please activate your account and choose your secure password within 48 hours:</p>
            <a href="{activation_url}" class="btn" style="color: #ffffff;">Set Password & Activate Account</a>
            <p style="font-size: 12px; color: #737487;">If the button does not work, copy and paste this link in your browser:<br><code>{activation_url}</code></p>
            <div class="footer">
                This single-use activation link expires in 48 hours.<br>
                G² Brothers CRM • Company Internal System
            </div>
        </div>
    </body>
    </html>
    """
    return await send_resend_email(to_email, subject, html)


async def send_password_reset_email(to_email: str, recipient_name: str, reset_token: str) -> dict:
    """Delivers password reset link with single-use expiring token."""
    reset_url = f"{settings.WEB_URL}/reset-password?token={reset_token}&email={to_email}"
    subject = "Password Reset Request - G² Brothers CRM"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F7F6F8; color: #292A3A; padding: 24px; }}
            .card {{ background-color: #FFFFFF; border-radius: 12px; border: 1px solid #E5E3EA; padding: 32px; max-width: 520px; margin: 0 auto; }}
            .brand {{ font-size: 20px; font-weight: 800; color: #292A3A; margin-bottom: 20px; }}
            .brand span {{ color: #72759A; }}
            .btn {{ display: inline-block; background-color: #4F5278; color: #FFFFFF; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: 600; margin: 20px 0; }}
            .footer {{ font-size: 12px; color: #737487; margin-top: 24px; border-top: 1px solid #E5E3EA; padding-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="brand">estate<span>flow</span> <span style="font-size: 11px; background: #E8E7F0; color: #4F5278; padding: 2px 6px; border-radius: 4px; font-weight: 700;">CRM</span></div>
            <h2>Password Reset</h2>
            <p>Hello {recipient_name},</p>
            <p>A password reset was requested for your account by an authorized administrator.</p>
            <a href="{reset_url}" class="btn" style="color: #ffffff;">Set New Password</a>
            <div class="footer">
                If you did not expect this request, contact your Super Admin immediately.<br>
                G² Brothers CRM
            </div>
        </div>
    </body>
    </html>
    """
    return await send_resend_email(to_email, subject, html)
