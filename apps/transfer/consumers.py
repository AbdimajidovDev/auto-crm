import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # User qaysi do'konga biriktirilganini tekshiramiz
        # Bu yerda user authentication (JWT/Session) ishlashi kerak
        self.user = self.scope["user"]
        if self.user.is_authenticated:
            # User do'konlari bo'yicha group nomini hosil qilamiz
            # Masalan: "store_1", "store_2"
            store_ids = await self.get_user_store_ids()
            for store_id in store_ids:
                await self.channel_layer.group_add(f"store_{store_id}", self.channel_name)
            await self.accept()
        else:
            await self.close()

    async def receive(self, text_data):
        pass # Clientdan xabar kutmaymiz

    async def send_notification(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    @database_sync_to_async
    def get_user_store_ids(self):
        return list(self.user.store_links.values_list('store_id', flat=True))
