from twitchio.ext import commands


class NotModeratorError(commands.GuardFailure):
    pass