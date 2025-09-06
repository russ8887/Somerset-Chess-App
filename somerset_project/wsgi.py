# somerset_project/wsgi.py

import os
from django.core.wsgi import get_wsgi_application

# ADD this import at the top
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')

application = get_wsgi_application()

# ADD this line to wrap the application with WhiteNoise
application = WhiteNoise(application)