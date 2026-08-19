from django.conf import settings
from django.core.mail import EmailMessage
from django.utils.html import escape


def notification_email(subject: str, body: str, to=None):
    recipients = to if to is not None else settings.LEAD_NOTIFICATION_EMAILS
    if not recipients:
        # No configured recipient is a misconfiguration, not a reason to raise
        # in the middle of a form submission the visitor is waiting on.
        print(f"No recipients configured for notification email: {subject}")
        return

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        reply_to=["hello@akiyainjapan.com"],
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


def refer_lead_to_agent(interest_request):
    """Hand a qualified lead to the licensed agent, with the qualification
    answers inline.

    Deliberately explicit rather than automatic on form submission: a lead is
    worth far more than the free forward it used to get, and the agent should
    only ever see leads that have already been worked. Returns True when a
    referral was actually sent.
    """
    recipients = settings.AGENT_NOTIFICATION_EMAILS
    if not recipients:
        return False

    def field(label, value):
        return (
            f"<p style='margin:2px 0'><b>{label}:</b> "
            f"{escape(value) if value else '<i>not provided</i>'}</p>"
        )

    admin_url = (
        "https://akiyainjapan.com/admin/membership/interestrequest/"
        f"{interest_request.pk}/change/"
    )
    body = (
        f"<p>Referring a qualified buyer from akiyainjapan.com.</p>"
        + field("Name", interest_request.name)
        + field("Email", interest_request.email)
        + field("Region(s)", interest_request.regions)
        + field("Budget", interest_request.budget)
        + field("Timeline", interest_request.timeline)
        + field("Been to Japan", interest_request.visited_japan)
        + field("Their message", interest_request.message)
        + field("Property of interest", interest_request.property_url)
        + f"<p style='margin-top:16px'>Referral ref: <b>AIJ-{interest_request.pk}</b>"
        + " — please quote this so the outcome can be tracked back.</p>"
        + "<p>Could you confirm receipt, and let us know the outcome either way?"
        + " Knowing why a lead doesn't proceed is as useful as knowing when one does.</p>"
        + f"<p style='color:#888;font-size:12px'>Internal record: {admin_url}</p>"
    )

    notification_email(
        subject=(
            f"Referral AIJ-{interest_request.pk} — {interest_request.name} "
            f"({interest_request.budget or 'budget not given'})"
        ),
        body=body,
        to=recipients,
    )
    return True


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


def notify_inspection_request(inspection):
    """Email whoever handles inspections, the moment one is requested.

    Sent immediately rather than batched because this lead decays: the listing can
    sell while the request sits in a table nobody is watching, and then there is
    nothing to inspect.

    Deliberately plain text with everything needed to act in the body — the point
    is to be able to reply from a phone without opening the admin.
    """
    from django.conf import settings
    from django.core.mail import EmailMessage

    lines = [
        f"Inspection requested — #{inspection.pk}",
        "",
        f"  From:     {inspection.name or '(no name given)'} <{inspection.email}>",
        f"  Account:  {'yes' if inspection.user else 'not signed in'}",
    ]
    if inspection.listing_location:
        lines.append(f"  Property: {inspection.listing_location}")
    if inspection.listing:
        lines.append(f"  Price:    {inspection.listing.get_price_for_front}")
    if inspection.listing_url:
        lines.append(f"  Listing:  {inspection.listing_url}")
    if inspection.notes:
        lines += ["", "  They said:", f"    {inspection.notes}"]
    lines += [
        "",
        "Next: check the listing is still available and that the agent will allow",
        "access, then get a price from the inspector and reply with a quote.",
        "",
        "Nothing has been charged.",
    ]

    message = EmailMessage(
        subject=f"Inspection request #{inspection.pk} — {inspection.listing_location or 'no location'}",
        body="\n".join(lines),
        to=[settings.CONSULT_NOTIFY_EMAIL],
        reply_to=[inspection.email] if inspection.email else None,
    )
    return message.send(fail_silently=False)
