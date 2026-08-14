from app.models import Customer
from app.repositories.base import BaseRepository

class CustomerRepository(BaseRepository):
    model = Customer