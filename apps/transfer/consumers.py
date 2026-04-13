from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async

from apps.store.models import StoreUser

class NotificationConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.groups_list = []

        user = self.scope["user"]

        if user.is_anonymous:
            await self.close(code=4001)
            return

        group = f"user_{user.id}"
        await self.channel_layer.group_add(group, self.channel_name)
        self.groups_list.append(group)

        await self.accept()

    async def notify(self, event):
        await self.send_json(event["data"])

    async def disconnect(self, close_code):
        for group in getattr(self, "groups_list", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def transfer_created(self, event):
        await self.send_json(event["data"])
