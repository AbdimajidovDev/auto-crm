from django.urls import path, include


urlpatterns = [
    path('users/', include('apps.users.urls')),
    path('store/', include('apps.store.urls')),
    path('contract/', include('apps.contract.urls')),
    path('products/', include('apps.products.urls')),
    path('transfer/', include('apps.transfer.urls')),
]
