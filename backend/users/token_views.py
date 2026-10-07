from rest_framework_simplejwt.views import TokenObtainPairView
from .token_serializers import EmailTokenObtainPairSerializer, EmailTokenObtainPairView

# Export for backwards compatibility
__all__ = ['EmailTokenObtainPairView', 'EmailTokenObtainPairSerializer']
