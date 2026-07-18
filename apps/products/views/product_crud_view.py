from django.db.models import ProtectedError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.products.filters import ProductFilter
from apps.products.models import Product, ProductBatch, ProductImage
from apps.products.serializers import ProductBatchListSerializer
from apps.products.serializers.product_crud_serializer import (
    ProductCreateSerializer,
    ProductGetSerializer,
    ProductListSerializer,
    # ProductBatchSerializer,
    ProductUpdateSerializer,
)
from apps.products.services.product_crud_service import ProductService
from apps.inventory.services import LowStockService

from django.db.models import Prefetch

from apps.store.models import Store


from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, permissions
from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from apps.products.services.product_query_service import (
    LOW_STOCK_THRESHOLD,
    annotate_stock_qty,
    apply_stock_status,
    apply_token_search,
)


# @extend_schema(
#     tags=["Product"],
#     summary="- Productlar ro'yxati.",
# )
# class ProductListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = ProductListSerializer
#
#     def get(self, request):
#         store_id = request.query_params.get("store")
#
#         if not store_id:
#             return Response({"error": "store param required"}, status=400)
#
#         queryset = Product.objects.filter(
#             is_active=True,
#             batches__store_id=store_id,
#             batches__is_active=True
#         ).annotate(
#             total_quantity=Sum("batches__quantity")
#         ).filter(
#             total_quantity__gt=0
#         ).distinct()
#
        # # 🔎 optional filters
        # search = request.query_params.get("search")
        # category = request.query_params.get("category")
        #
        # if search:
        #     queryset = queryset.filter(name__icontains=search)
        #
        # if category:
        #     queryset = queryset.filter(category_id=category)
        #
        # serializer = self.serializer_class(
        #     queryset,
        #     many=True,
        #     context={"request": request}
        # )
        # return Response(serializer.data, status=200)



# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────
# class ProductListAPIView(generics.ListAPIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = ProductListSerializer
#     pagination_class = StandardPagination
#
#     filter_backends = [DjangoFilterBackend, SearchFilter]
#     filterset_class = ProductFilter
#     search_fields = ["name", "description"]  # ?search=moy
#
#     def get_queryset(self):
#         # ✅ YAXSHI: Product list uchun `select_related` va `Prefetch` ishlatilgan, serializerdagi nested maydonlar N+1 chiqarmaydi.
#         # Prefetch bilan batches ichidagi store va location ham bitta queryda keladi.
#         # Jami: 1 (Product) + 1 (images) + 1 (batches) = 3 SQL query.
#         # select_related category va unit_measurement JOIN qiladi → +0 query.
#         batches_qs = ProductBatch.objects.select_related(
#             "store", "location"
#         ).filter(is_active=True)
#
#         return (
#             Product.objects
#             .filter(is_active=True)
#             .select_related("category", "unit_measurement")
#             .prefetch_related(
#                 Prefetch("images"),
#                 Prefetch("batches", queryset=batches_qs),
#             )
#             .order_by("name")
#         )


# ============================================================
# VIEW
# ============================================================

@extend_schema(
    tags=["Product"],
    summary="Productlar ro'yxati (search, filter, pagination, stats bilan)",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR,
                         description="Token qidiruv: so'zlar tartibidan qat'i nazar (nom lotin/kirill, "
                                     "SKU, barcode, tavsif bo'yicha). Masalan 'cobalt dvornik' = 'dvornik cobalt'"),
        OpenApiParameter("category", OpenApiTypes.INT,
                         description="Kategoriya ID bo'yicha filter"),
        OpenApiParameter("is_active", OpenApiTypes.BOOL,
                         description="Faollik holati bo'yicha filter"),
        OpenApiParameter("store_id", OpenApiTypes.INT,
                         description="Do'kon ID — faqat shu do'konda mavjud mahsulotlar, "
                                     "do'kondagi qoldiq bo'yicha kamayish tartibida. "
                                     "stock_status va stats ham shu do'kon qoldig'i bo'yicha hisoblanadi"),
        OpenApiParameter("stock_status", OpenApiTypes.STR,
                         description="Qoldiq holati: in_stock (>5), low_stock (1..5), out_of_stock (0). "
                                     "store_id berilsa — shu do'kon qoldig'i, bo'lmasa barcha do'konlar jami"),
        OpenApiParameter("page", OpenApiTypes.INT,
                         description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT,
                         description="Sahifadagi yozuvlar soni (max 100)"),
    ],
)
class ProductListAPIView(generics.ListAPIView):
    permission_classes = [
        permissions.IsAuthenticated
    ]

    serializer_class = ProductListSerializer
    pagination_class = StandardPagination

    filter_backends = [
        DjangoFilterBackend,
    ]

    filterset_class = ProductFilter

    def get_queryset(self):

        active_batches = (
            ProductBatch.objects
            .filter(
                is_active=True
            )
            .select_related(
                "store",
                "location",
            )
            .only(
                "id",
                "product_id",
                "quantity",
                # Serializer (get_batches) ishlatadigan narx/holat maydonlari —
                # only() dan tushib qolsa har bir batch uchun alohida deferred
                # SQL chiqadi (N+1). 100 mahsulotda ~1600 so'rovga yetgan edi.
                "purchase_price",
                "selling_price",
                "wholesale_price",
                "is_active",
                "store_id",
                "store__id",
                "store__name",
                "location_id",
                "location__id",
                # location tarjima qilinadigan maydon (modeltranslation) —
                # barcha til variantlari kerak, aks holda deferred so'rov chiqadi
                "location__location",
                "location__location_uz",
                "location__location_uz_cyrl",
            )
        )

        queryset = (
            Product.objects
            .filter(
                status=Product.ProductStatus.ACTIVE
            )
            .select_related(
                "category",
                "brand",
                "unit_measurement",
            )
            .prefetch_related(

                Prefetch(
                    "images",
                    queryset=(
                        ProductImage.objects
                        .only(
                            "id",
                            "product_id",
                            "image",
                        )
                    ),
                ),

                Prefetch("batches", queryset=active_batches,),)
            # DIQQAT: bu yerda .only() ataylab YO'Q. Product/Category/Brand/
            # UnitMeasurement maydonlari modeltranslation bilan tarjima qilinadi
            # (name_uz, name_uz_cyrl, ...) va serializer status/created_at/
            # shtrix_code kabi maydonlarni ham o'qiydi. Tor only() ro'yxati har
            # bir yetishmagan maydon uchun mahsulot boshiga alohida deferred SQL
            # chiqarardi — 100 mahsulotda 1674 ta so'rov, ~1.5s. To'liq qatorni
            # bitta so'rovda olish bundan ancha arzon (~6 SQL, <0.3s).
        )

        # Umumiy logika (eksport bilan bir xil): token qidiruv + stock_qty annotatsiyasi
        queryset = apply_token_search(queryset, self.request.query_params.get("search"))

        store_id = self.request.query_params.get("store_id")
        if store_id and store_id.isdigit():
            queryset = annotate_stock_qty(queryset, store_id).order_by("-stock_qty", "name")
        else:
            # Qoldig'i ko'p mahsulotlar yuqorida; -id — pagination barqarorligi uchun tiebreaker
            queryset = annotate_stock_qty(queryset).order_by("-stock_qty", "-id")

        return queryset

    def list(self, request, *args, **kwargs):
        # search/category/store filtrlangan (lekin stock_status QO'LLANMAGAN) queryset —
        # stats shu asosda hisoblanadi, shunda tab sonlari doim to'liq ko'rinadi
        base_queryset = self.filter_queryset(self.get_queryset())

        stats = base_queryset.aggregate(
            all=Count("id"),
            in_stock=Count("id", filter=Q(stock_qty__gt=LOW_STOCK_THRESHOLD)),
            low_stock=Count("id", filter=Q(stock_qty__gt=0, stock_qty__lte=LOW_STOCK_THRESHOLD)),
            out_of_stock=Count("id", filter=Q(stock_qty__lte=0)),
        )

        queryset = apply_stock_status(
            base_queryset, request.query_params.get("stock_status")
        )

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["stats"] = stats
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({"results": serializer.data, "stats": stats})

    def get_serializer_context(self):

        context = super().get_serializer_context()

        stores = (
            Store.objects
            .filter(
                is_active=True
            )
            .only(
                "id",
                "name",
            )
            .order_by(
                "name"
            )
        )

        context["all_stores"] = stores

        return context


# class ProductListAPIView(generics.ListAPIView):
#     permission_classes  = [permissions.IsAuthenticated]
#     serializer_class    = ProductListSerializer
#     pagination_class    = StandardPagination
#     filter_backends     = [DjangoFilterBackend, SearchFilter]
#     filterset_class     = ProductFilter
#     search_fields       = ["id", "name", "description"]
#
#     def get_queryset(self):
#         batches_qs = (
#             ProductBatch.objects
#             .select_related("store", "location")
#             .filter(is_active=True)
#         )
#         return (
#             Product.objects
#             .filter(is_active=True)
#             .select_related("category", "unit_measurement")
#             .prefetch_related(
#                 Prefetch("images"),
#                 Prefetch("batches", queryset=batches_qs),
#             )
#             .order_by("name")
#         )
#
#     def get_serializer_context(self):
#         context = super().get_serializer_context()
#         # Barcha faol do'konlar — bitta query, context orqali serializer ga uzatiladi.
#         # Har bir mahsulot uchun alohida Store query bo'lmaydi → N+1 yo'q.
#         context["all_stores"] = list(
#             Store.objects.filter(is_active=True).only("id", "name").order_by("name")
#         )
#         return context
#


@extend_schema(
    tags=["Product"],
    summary="- Product yaratish.",
)
class ProductCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductCreateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = ProductService.create_product(serializer.validated_data)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(ProductGetSerializer(product).data, status=201)


class ProductDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductUpdateSerializer

    @extend_schema(
        tags=["Product"],
        summary="- ID orqali Product malumotlarini olish.",
    )
    def get(self, request, pk):
        # ⚠️ MUAMMO [PERFORMANCE]: Detail queryset `select_related/prefetch_related` ishlatmayapti.
        # Sabab: `ProductUpdateSerializer` hozir yengil, lekin detail javob kengaysa category/unit/images uchun N+1 yoki ortiqcha query chiqadi.
        # Natija: detail endpoint listdagi optimizatsiyadan farqli ishlaydi va kelajakdagi o'zgarishda sekinlashadi.
        # ✅ YECHIM:
        # product = get_object_or_404(
        #     Product.objects.select_related("category", "unit_measurement").prefetch_related("images", "batches"),
        #     pk=pk,
        # )
        product = get_object_or_404(Product, pk=pk)
        serializer = self.serializer_class(product)
        return Response(serializer.data, status=200)

    @extend_schema(
        tags=["Product"],
        summary="- Product va uning rasmlarini tahrirlash",
    )
    def put(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        old_min_stock = product.min_stock

        # RBAC: arxivlash (statusni inactive qilish) alohida huquq talab qiladi
        from apps.users.permissions import user_has_perm
        wants_archive = (
            str(request.data.get("status", "")) == Product.ProductStatus.INACTIVE
            and product.status != Product.ProductStatus.INACTIVE
        )
        if wants_archive and not user_has_perm(request.user, "products.archive"):
            return Response(
                {"detail": "Ruxsat yo'q: mahsulotni arxivlash uchun 'products.archive' huquqi kerak."},
                status=403,
            )

        serializer = ProductUpdateSerializer(
            product,
            data=request.data,
            partial=True
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Threshold o'zgarsa, mahsulotni saqlovchi barcha do'konlar bo'yicha
        # low-stock holatini qayta baholaymiz (on_commit orqali).
        if product.min_stock != old_min_stock:
            LowStockService.reevaluate_product(product)

        return Response('Product successfully updated!', status=200)


    @extend_schema(
        tags=["Product"],
        summary="- Productni o'chirish.",
    )
    def delete(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        try:
            product.delete()
        except ProtectedError:
            raise ValidationError(
                "Bu mahsulotni o‘chirib bo‘lmaydi, chunki u allaqachon tizimda ishlatilgan (kirim/sotuv mavjud)."
            )

        return Response(status=204)


        # if product.is_active:
        #     product.is_active = False
        #     product.save()
        # return Response('Product not found', status=400)



# @extend_schema(
#     tags=["Product"],
#     summary="- barcode orqali productni olish.",
# )
# class BatchByBarcodeAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#
#     def get(self, request, barcode):
#         batch = get_object_or_404(ProductBatch, barcode=barcode)
#         serializer = ProductBatchSerializer(batch, context={"request": request})
#         return Response(serializer.data, status=200)

#
# class ProductBatchListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = ProductBatchSerializer
#
#     def get(self, request):
#         products = ProductBatch.objects.filter(is_active=True)
#         serializer = self.serializer_class(products, many=True, context={"request": request})
#         return Response(serializer.data, status=200)



class ProductBatchListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductBatchListSerializer

    def get_store(self, request):
        user = request.user
        store_id = request.query_params.get("store")

        # 🔥 SUPERUSER LOGIC
        if user.is_superuser:
            if store_id:
                return Store.objects.get(id=store_id)

            # default → SKLAD
            return Store.objects.get(type='b')

        # 🔒 SELLER LOGIC
        # ⚠️ MUAMMO [KRITIK]: Oddiy user uchun `user.store` maydoni ishlatilgan, lekin modelda asosiy bog'lanish `StoreUser` orqali ko'rinyapti.
        # Sabab: boshqa joylarda user-store access `StoreUser.objects.filter(user=user, is_active=True)` bilan tekshiriladi.
        # Natija: AttributeError yoki noto'g'ri store tanlanishi mumkin.
        # ✅ YECHIM:
        # store_link = StoreUser.objects.select_related("store").filter(user=user, is_active=True).first()
        # if not store_link:
        #     raise ValidationError("User store bilan bog'lanmagan")
        # return store_link.store
        return user.store  # ⚠️ shu joyni aniqlab ol

    @extend_schema(
        tags=["Product"],
        summary="- Sotuv paneli uchun Mahsulotlar ro'yxati.",
    )
    def get(self, request):
        selected_store = self.get_store(request)

        search = request.query_params.get("search")

        # ⚠️ MUAMMO [PERFORMANCE]: ProductBatchListAPIView pagination ishlatmaydi.
        # Sabab: barcha active productlar serializerga beriladi; `all_batches` ham hammasi prefetch qilinadi.
        # Natija: sotuv panelida productlar ko'payganda response hajmi va xotira sarfi oshadi.
        # ✅ YECHIM:
        # page = self.paginate_queryset(products)
        # return self.get_paginated_response(serializer.data)
        products = Product.objects.filter(is_active=True)

        if search:
            products = products.filter(name__icontains=search)

        products = products.prefetch_related(
            Prefetch(
                "batches",
                queryset=ProductBatch.objects.filter(is_active=True).select_related("store"),
                to_attr="all_batches"
            )
        )

        serializer = self.serializer_class(
            products,
            many=True,
            context={"selected_store": selected_store}
        )

        return Response(serializer.data)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ProductBatchListAPIView.get_store oddiy user store resolverini StoreUser orqali tuzatish]
# ═══════════════════════════════
