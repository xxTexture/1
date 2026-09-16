import discord
import datetime
import re
import io


async def create_transcript(channel: discord.TextChannel) -> discord.File:
    """Генерирует текстовый транскрипт канала."""
    lines = [
        f"Транскрипт канала: #{channel.name}",
        f"Дата генерации: {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "-" * 60,
        "",
    ]
    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {msg.clean_content}")
        for a in msg.attachments:
            lines.append(f"  [Вложение: {a.url}]")
        for e in msg.embeds:
            lines.append(f"  [Embed: {e.title or '(без заголовка)'}]")

    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    return discord.File(fp=buf, filename=f"transcript-{channel.name}.txt")


def parse_duration(time_str: str) -> datetime.timedelta:
    """Парсит строку вида '10m', '2h', '1d', '30s' в timedelta."""
    if not time_str:
        return datetime.timedelta(hours=1)
    m = re.match(r"(\d+)\s*([smhd])", time_str.lower().strip())
    if m:
        v, u = int(m.group(1)), m.group(2)
        return {
            "s": datetime.timedelta(seconds=v),
            "m": datetime.timedelta(minutes=v),
            "h": datetime.timedelta(hours=v),
            "d": datetime.timedelta(days=v),
        }.get(u, datetime.timedelta(hours=1))
    return datetime.timedelta(hours=1)


def duration_to_str(td: datetime.timedelta) -> str:
    """Форматирует timedelta в читаемую строку."""
    total = int(td.total_seconds())
    if total < 60:
        return f"{total} сек."
    if total < 3600:
        return f"{total // 60} мин."
    if total < 86400:
        return f"{total // 3600} ч."
    return f"{total // 86400} дн."


def mentions_from_ids(guild: discord.Guild, role_ids: list[int]) -> str:
    """Возвращает строку упоминаний ролей по их ID."""
    mentions = []
    for rid in role_ids:
        if rid:
            role = guild.get_role(rid)
            if role:
                mentions.append(role.mention)
    return " ".join(mentions) if mentions else ""
