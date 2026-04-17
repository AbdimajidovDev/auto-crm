from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.reports.services import DashboardReportService, DashboardService
from apps.reports.serializers import DashboardReportSerializer
from apps.reports.services.chart_service import ChartService
from apps.reports.utils.date_filters import DateRangeResolver
from apps.sales.models import Sale


@extend_schema(
    tags=['Dashboard'],
    summary='Dashboard uchun Hisobot',
)
class DashboardReportAPIView(APIView):
    def get(self, request):
        data = DashboardReportService.get_dashboard_data()
        serializer = DashboardReportSerializer(data)
        return Response(serializer.data)


# api/dashboard.py

from rest_framework.views import APIView
from rest_framework.response import Response


@extend_schema(
    tags=['Dashboard'],
    summary='Dashboard uchun Hisobot',
)
class DashboardAPIView(APIView):

    def get(self, request):

        filter_type = request.GET.get("filter", "monthly")
        from_date = request.GET.get("from")
        to_date = request.GET.get("to")

        date_from, date_to = DateRangeResolver.resolve(
            filter_type, from_date, to_date
        )

        reports = DashboardService.get_reports(
            request.user,
            date_from,
            date_to
        )

        sales_qs = Sale.objects.filter(
            created_at__range=(date_from, date_to)
        )

        chart = ChartService.get_turnover_chart(
            sales_qs,
            filter_type
        )

        return Response({
            "dashboard": {
                "filters": {
                    "selected": filter_type,
                },
                "reports": reports,
                "charts": {
                    "turnover": chart
                }
            }
        })
