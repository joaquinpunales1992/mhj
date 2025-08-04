from django.core.mail import EmailMessage


def notification_email(subject: str, body: str):
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email="hello@myakiyainjapan.com",
        to=["joaquinpunales@gmail.com"],
        reply_to=["hello@myakiyainjapan.com"],
    )

    email.content_subtype = "html"
    try:
        email.send()
    except Exception as e:
        print(f"Error sending email: {e}")


def notify_social_token_expired(message=None):
    return notification_email(subject="Your Akiya in Japan - SOCIAL TOKEN EXPIRED", body=message)
    

def notify_user_registered_via_email(new_user_email):
    return notification_email(subject="Your Akiya in Japan - NEW USER REGISTERD VIA EMAIL", body=f"New User Registered via Emial: {new_user_email}")
    

async def notify_user_expressed_interest(email: str):
    notification_email(subject="USER EXPRESSED INTERESTS", body=f"USER EXPRESSED INTEREST: {email}")
