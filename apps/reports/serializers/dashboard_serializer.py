from rest_framework import serializers

class DashboardReportSerializer(serializers.Serializer):
    total_products_in_stock = serializers.IntegerField()
    monthly_revenue = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_customer_debt = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_supplier_debt = serializers.DecimalField(max_digits=15, decimal_places=2)
    report_date = serializers.DateTimeField()