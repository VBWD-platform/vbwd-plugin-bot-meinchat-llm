"""SQLAlchemy models for the bot-meinchat-llm plugin.

Importing this package registers every model class on the shared
``db.metadata`` so ``create_all`` / the integration harness can build the
plugin's tables.
"""
from plugins.bot_meinchat_llm.bot_meinchat_llm.models.rag_chunk import (  # noqa: F401
    RagChunk,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.models.room_coupon import (  # noqa: F401,E501
    RoomCoupon,
)

__all__ = ["RagChunk", "RoomCoupon"]
