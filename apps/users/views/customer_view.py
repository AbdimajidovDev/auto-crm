from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models.customers import Customer
from apps.users.serializers.customer_serializer import CustomerSerializer


@extend_schema(
    tags=['customer'],
    summary="Mijozlar ro'yxati",
)
class CustomerListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return Customer.objects.all()


@extend_schema(
    tags=['customer'],
    summary="Mijoz yaratish",
)
class CustomerCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerSerializer


@extend_schema(
    tags=['customer'],
    summary="Mijoz",
)
class CustomerDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerSerializer

    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        serializer = self.serializer_class(customer)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        serializer = self.serializer_class(customer, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        customer.delete()
        return Response('Customer successfully deleted!', status=status.HTTP_204_NO_CONTENT)
