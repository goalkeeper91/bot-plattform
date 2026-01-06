from pydantic import BaseModel

class TwitchCommandEvent(BaseModel):
    user: str
    command: str
    channel: str
