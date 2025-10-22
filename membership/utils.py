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
    return notification_email(
        subject="Your Akiya in Japan - SOCIAL TOKEN EXPIRED", body=message
    )


def notify_user_registered_via_email(new_user_email):
    return notification_email(
        subject="Your Akiya in Japan - NEW USER REGISTERD VIA EMAIL",
        body=f"New User Registered via Emial: {new_user_email}",
    )


from django.views.decorators.http import require_POST
from django.http import JsonResponse
import json

@require_POST
def notify_user_expressed_interest(request):
    try:
        # Parse JSON body
        data = json.loads(request.body)
        email = data.get('email')
        section = data.get('section')
        property_id = data.get('property_id')
                
        # Send your notification email
        notification_email(
            subject=f"USER EXPRESSED INTERESTS - {section}", 
            body=f"USER EXPRESSED INTEREST: {email}, Section: {section}, Property ID: {property_id}"
        )
        
        return JsonResponse({'status': 'success'})
        
    except Exception as e:
        print(f"Error: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
