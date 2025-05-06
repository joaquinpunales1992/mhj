import json
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.template.loader import render_to_string


def notify_user_registered_via_email(new_user_email):

    email = EmailMessage(
        subject='Your House in Japan - NEW USER REGISTERD VIA EMAIL',
        body=f"New User Registered via Emial: {new_user_email}",
        from_email='noreply@myhouseinjapan.com',
        to=['joaquinpunales@gmail.com'],
        reply_to=['noreply@myhouseinjapan.com']
    )

    email.content_subtype = 'html'
    try:
        email.send()
    except Exception as e:
        print(f"Error sending email: {e}")