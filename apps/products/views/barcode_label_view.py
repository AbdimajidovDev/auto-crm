from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.users.permissions import RequirePermission
from apps.products.models import BarcodeTemplate, Product
from apps.products.serializers.barcode_template_serializer import (
    BarcodeTemplateListSerializer,
    BarcodeTemplateDetailSerializer,
    BarcodeLabelPreviewRequestSerializer,
    BarcodeLabelPrintRequestSerializer,
)
from apps.products.services.label_engine import (
    LabelTemplateValidator,
    LabelDataResolver,
    PdfLabelRenderer,
    PngLabelRenderer,
    LabelValidationError,
    LabelResolutionError,
    LabelRenderingError,
)


class BarcodeTemplateViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Barcode Templates.
    - Global templates (no store FK)
    - Single active default template enforcement
    - Soft delete protection for default template
    - Duplicate and Set-Default custom actions
    """
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        action_perm_map = {
            "list": "barcode_templates.view",
            "retrieve": "barcode_templates.view",
            "create": "barcode_templates.create",
            "duplicate": "barcode_templates.create",
            "update": "barcode_templates.edit",
            "partial_update": "barcode_templates.edit",
            "set_default": "barcode_templates.edit",
            "destroy": "barcode_templates.delete",
        }
        required_code = action_perm_map.get(self.action, "barcode_templates.view")
        return [IsAuthenticated(), RequirePermission(required_code)]

    def get_serializer_class(self):
        if self.action == "list":
            return BarcodeTemplateListSerializer
        return BarcodeTemplateDetailSerializer

    def get_queryset(self):
        qs = BarcodeTemplate.objects.all().select_related("created_by")
        include_inactive = self.request.query_params.get("include_inactive", "").lower() == "true"
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-is_default", "-id")

    def perform_create(self, serializer):
        with transaction.atomic():
            if serializer.validated_data.get("is_default"):
                BarcodeTemplate.objects.filter(is_default=True).update(is_default=False)
            serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            if serializer.validated_data.get("is_default"):
                BarcodeTemplate.objects.exclude(id=serializer.instance.id).filter(is_default=True).update(is_default=False)
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_default:
            return Response(
                {"detail": "Standart (default) shablonni o'chirib bo'lmaydi. Avval boshqa shablonni standart qilib belgilang."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        instance = self.get_object()
        base_name = f"{instance.name} (Nusxa)"
        candidate_name = base_name
        counter = 2
        while BarcodeTemplate.objects.filter(name=candidate_name).exists():
            candidate_name = f"{base_name} {counter}"
            counter += 1

        new_template = BarcodeTemplate.objects.create(
            name=candidate_name,
            description=instance.description,
            width_mm=instance.width_mm,
            height_mm=instance.height_mm,
            barcode_format=instance.barcode_format,
            layout=instance.layout,
            is_default=False,
            is_active=True,
            created_by=request.user,
        )
        serializer = BarcodeTemplateDetailSerializer(new_template)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        instance = self.get_object()
        with transaction.atomic():
            BarcodeTemplate.objects.filter(is_default=True).update(is_default=False)
            instance.is_default = True
            instance.is_active = True
            instance.save(update_fields=["is_default", "is_active"])
        serializer = BarcodeTemplateDetailSerializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BarcodeLabelPreviewAPIView(APIView):
    """
    Generates a single label PNG image preview stream for the visual designer.
    Supports layout_override for unsaved, real-time live preview.
    """
    permission_classes = [IsAuthenticated, RequirePermission]
    required_permission = "barcode_templates.view"

    def post(self, request, *args, **kwargs):
        serializer = BarcodeLabelPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data["product_id"]
        template_id = serializer.validated_data.get("template_id")
        store_id = serializer.validated_data.get("store_id")
        layout_override = serializer.validated_data.get("layout_override")

        product = get_object_or_404(
            Product.objects.select_related("brand", "category", "unit_measurement").prefetch_related("images"),
            id=product_id,
        )

        if template_id:
            template = get_object_or_404(BarcodeTemplate, id=template_id, is_active=True)
        else:
            template = BarcodeTemplate.objects.filter(is_default=True, is_active=True).first()
            if not template:
                template = BarcodeTemplate.objects.filter(is_active=True).first()
            if not template:
                return Response({"detail": "Faol shtrix-kod shabloni topilmadi."}, status=status.HTTP_404_NOT_FOUND)

        width_mm = template.width_mm
        height_mm = template.height_mm

        try:
            if layout_override:
                layout, _ = LabelTemplateValidator.validate(layout_override, width_mm, height_mm)
            else:
                layout = template.layout

            store = LabelDataResolver.resolve_store(request.user, store_id)
            context = LabelDataResolver.resolve_context(product, store)
            png_bytes = PngLabelRenderer.render_png(width_mm, height_mm, layout, context)
        except (LabelValidationError, LabelResolutionError) as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except LabelRenderingError as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return HttpResponse(png_bytes, content_type="image/png")


class BarcodeLabelPrintAPIView(APIView):
    """
    Generates a vector multi-copy PDF stream ready for direct thermal printing.
    """
    permission_classes = [IsAuthenticated, RequirePermission]
    required_permission = "barcode_templates.view"

    def post(self, request, *args, **kwargs):
        serializer = BarcodeLabelPrintRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        template_id = serializer.validated_data.get("template_id")
        store_id = serializer.validated_data.get("store_id")
        items_input = serializer.validated_data["items"]

        if template_id:
            template = get_object_or_404(BarcodeTemplate, id=template_id, is_active=True)
        else:
            template = BarcodeTemplate.objects.filter(is_default=True, is_active=True).first()
            if not template:
                template = BarcodeTemplate.objects.filter(is_active=True).first()
            if not template:
                return Response({"detail": "Faol shtrix-kod shabloni topilmadi."}, status=status.HTTP_404_NOT_FOUND)

        try:
            store = LabelDataResolver.resolve_store(request.user, store_id)
        except LabelResolutionError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        product_ids = [it["product_id"] for it in items_input]
        products_map = {
            p.id: p
            for p in Product.objects.filter(id__in=product_ids)
            .select_related("brand", "category", "unit_measurement")
            .prefetch_related("images")
        }

        items_to_render = []
        for item in items_input:
            product = products_map.get(item["product_id"])
            if not product:
                continue
            context = LabelDataResolver.resolve_context(product, store)
            items_to_render.append((context, item.get("quantity", 1)))

        if not items_to_render:
            return Response({"detail": "Chop etish uchun birorta tovar topilmadi."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            pdf_bytes = PdfLabelRenderer.render_pdf(
                template.width_mm,
                template.height_mm,
                template.layout,
                items_to_render,
            )
        except LabelRenderingError as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="barcode_labels.pdf"'
        return response
