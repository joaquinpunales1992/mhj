from django.shortcuts import render

def display_home(request):
    """
    View function to render the home page.
    """
    return render(request, 'home.html')