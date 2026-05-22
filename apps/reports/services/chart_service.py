from django.db.models.functions import TruncWeek, TruncMonth, TruncDay
from django.db.models import Sum, F, ExpressionWrapper, DecimalField


class ChartService:

    @staticmethod
    def get_turnover_chart(sales_qs, filter_type):

        if filter_type == "weekly":
            trunc = TruncDay("created_at")
            label_format = "%Y-%m-%d"

        elif filter_type == "monthly":
            trunc = TruncWeek("created_at")
            label_format = "Week %W"

        else:  # yearly
            trunc = TruncMonth("created_at")
            label_format = "%Y-%m"

        data = (
            sales_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(total=Sum("total_amount"))
            .order_by("period")
        )

        # ✅ YAXSHI: Chart grouping DB darajasida `Trunc*` va aggregate bilan bajarilgan.
        labels = [i["period"].strftime(label_format) for i in data]
        values = [i["total"] for i in data]

        return {
            "labels": labels,
            "data": values
        }


    @staticmethod
    def profit_trend(items_qs, filter_type):

        trunc = TruncWeek("sale__created_at") if filter_type == "monthly" else TruncMonth("sale__created_at")

        data = (
            items_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(
                profit=Sum(
                    ExpressionWrapper(
                        # ⚠️ MUAMMO [PERFORMANCE/ANIQLIK]: `purchase_price` NULL bo'lsa profit ifodasi NULL bo'ladi.
                        # Sabab: `Coalesce(F("purchase_price"), 0)` ishlatilmagan.
                        # Natija: foyda trendida qiymatlar yo'qolishi yoki noto'g'ri chiqishi mumkin.
                        # ✅ YECHIM:
                        # (F("unit_price") - Coalesce(F("purchase_price"), Value(0))) * F("quantity")
                        # Eslatma: `purchase_price` NULL bo'lsa ifoda NULL — yig'indida foyda yo'qolishi mumkin.
                        (F("unit_price") - F("purchase_price")) * F("quantity"),
                        output_field=DecimalField()
                    )
                )
            )
            .order_by("period")
        )

        return {
            "labels": [i["period"].strftime("%Y-%m-%d") for i in data],
            "data": [i["profit"] for i in data]
        }



class ProfitChartService:

    @staticmethod
    def get(items_qs, filter_type):

        if filter_type == "monthly":
            trunc = TruncWeek("sale__created_at")
        else:
            trunc = TruncMonth("sale__created_at")

        data = (
            items_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(
                profit=Sum(
                    ExpressionWrapper(
                        # Eslatma: `purchase_price` NULL bo'lsa ifoda NULL — yig'indida foyda yo'qolishi mumkin.
                        (F("unit_price") - F("purchase_price")) * F("quantity"),
                        output_field=DecimalField()
                    )
                )
            )
            .order_by("period")
        )

        return data


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Profit hisobida `purchase_price` NULL holatini Coalesce bilan yopish]
# ═══════════════════════════════
