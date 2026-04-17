# api/top_products.py
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.reports.services.top_product_service import TopProductsService
from apps.reports.utils.date_filters import DateRangeResolver
from apps.reports.utils.date_parser import DateValidator


@extend_schema(
    tags=['Dashboard'],
    summary="Eng ko'p sotilgan mahsulotlar.",
)
class TopProductsAPIView(APIView):

    def get(self, request):

        filter_type = request.GET.get("filter", "daily")
        limit = int(request.GET.get("limit", 5))
        store_id = request.GET.get("store_id")

        from_date, to_date = DateValidator.validate(
            request.GET.get("from"),
            request.GET.get("to")
        )

        if not from_date:
            from_date, to_date = DateRangeResolver.resolve(filter_type)

        data = TopProductsService.get_top_products(
            user=request.user,
            date_from=from_date,
            date_to=to_date,
            limit=limit,
            store_id=store_id
        )

        return Response({
            "topProducts": data
        })
