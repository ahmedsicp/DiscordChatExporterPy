import argparse
import asyncio
import datetime
import html
import io
import json
import math
import os
import pathlib
import re
import time
import traceback
import unicodedata
import urllib.parse
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import aiohttp
import emoji
import pytz
from grapheme import graphemes
from pytz import timezone

# =========================================================================
# 1. DISCORD LIBRARY COMPATIBILITY
# =========================================================================
# Dynamically imports the available Discord library (discord.py, nextcord, or disnake)
discord_modules = ["nextcord", "disnake", "discord"]
for module in discord_modules:
    try:
        discord = __import__(module)
        discord.module = module
        break
    except ImportError:
        continue

# =========================================================================
# 2. UTILITIES & CONSTANTS
# =========================================================================
class DiscordUtils:
    """Stores CDN links for default Discord assets (logos, file icons, etc.)"""
    logo: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-logo.svg"
    default_avatar: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-default.png"
    pinned_message_icon: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-pinned.svg"
    thread_channel_icon: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-thread.svg"
    thread_remove_recipient: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-thread-remove-recipient.svg"
    thread_add_recipient: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-thread-add-recipient.svg"
    file_attachment_audio: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-audio.svg"
    file_attachment_acrobat: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-acrobat.svg"
    file_attachment_webcode: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-webcode.svg"
    file_attachment_code: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-code.svg"
    file_attachment_document: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-document.svg"
    file_attachment_archive: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-archive.svg"
    file_attachment_unknown: str = "https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-unknown.svg"
    button_external_link: str = '<img class="chatlog__reference-icon" src="https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-external-link.svg">'
    reference_attachment_icon: str = '<img class="chatlog__reference-icon" src="https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-attachment.svg">'
    interaction_command_icon: str = '<img class="chatlog__interaction-icon" src="https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-command.svg">'
    interaction_dropdown_icon: str = '<img class="chatlog__dropdown-icon" src="https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/discord-dropdown.svg">'

async def discriminator(user: str, discriminator: str):
    """Formats legacy Discord discriminators (e.g., User#1234) or modern usernames."""
    if discriminator != "0":
        return f"{user}#{discriminator}"
    return user

# =========================================================================
# 3. ASYNC CACHING SYSTEM
# =========================================================================
_internal_cache: dict = {}

def _wrap_and_store_coroutine(cache, key, coro):
    async def func():
        value = await coro
        cache[key] = value
        return value
    return func()

def _wrap_new_coroutine(value):
    async def new_coroutine():
        return value
    return new_coroutine()

def clear_cache():
    _internal_cache.clear()

def cache():
    """Decorator to cache asynchronous function returns based on arguments."""
    def decorator(func):
        def _make_key(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
            def _true_repr(o):
                if o.__class__.__repr__ is object.__repr__:
                    return f"<{o.__class__.__module__}.{o.__class__.__name__}>"
                return repr(o)
            key = [f"{func.__module__}.{func.__name__}"]
            key.extend(_true_repr(o) for o in args)
            for k, v in kwargs.items():
                key.append(_true_repr(k))
                key.append(_true_repr(v))
            return ":".join(key)

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = _make_key(args, kwargs)
            try:
                value = _internal_cache[key]
            except KeyError:
                value = func(*args, **kwargs)
                return _wrap_and_store_coroutine(_internal_cache, key, value)
            else:
                return _wrap_new_coroutine(value)

        wrapper.cache = _internal_cache
        wrapper.clear_cache = _internal_cache.clear
        return wrapper
    return decorator

# =========================================================================
# 4. EMOJI CONVERSION
# =========================================================================
# Maps standard unicode emojis to Twemoji (Twitter Emoji) CDN links for cross-platform rendering
cdn_fmt = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{codepoint}.png"

@cache()
async def valid_src(src):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(src) as resp:
                return resp.status == 200
    except aiohttp.ClientConnectorError:
        return False

def valid_category(char):
    try:
        return unicodedata.category(char) == "So"
    except TypeError:
        return False

async def codepoint(codes):
    if "200d" not in codes:
        return "-".join([c for c in codes if c != "fe0f"])
    return "-".join(codes)

async def convert(char):
    if valid_category(char):
        name = unicodedata.name(char).title()
    else:
        if len(char) == 1:
            return char
        else:
            shortcode = emoji.demojize(char)
            name = shortcode.replace(":", "").replace("_", " ").replace("selector", "").title()
    
    src = cdn_fmt.format(codepoint=await codepoint(["{cp:x}".format(cp=ord(c)) for c in char]))
    if await valid_src(src):
        return f'<img class="emoji emoji--small" src="{src}" alt="{char}" title="{name}" aria-label="Emoji: {name}">'
    return char

async def convert_emoji(string):
    """Parses a string and replaces unicode emojis with HTML image tags."""
    x = [await convert(ch) for ch in graphemes(string)]
    return "".join(x)

# =========================================================================
# 5. AST & MARKDOWN PARSING (Discord Syntax to HTML)
# =========================================================================
class Node:
    def render(self, guild=None, bot=None) -> str:
        raise NotImplementedError()

class TextNode(Node):
    def __init__(self, text: str):
        self.text = text
    def render(self, guild=None, bot=None):
        return self.text

class ContainerNode(Node):
    def __init__(self, children: List[Node]):
        self.children = children
    def render_children(self, guild=None, bot=None):
        return "".join(c.render(guild, bot) for c in self.children)

class BoldNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f"<strong>{self.render_children(guild, bot)}</strong>"

class ItalicNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f"<em>{self.render_children(guild, bot)}</em>"

class UnderlineNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f'<span style="text-decoration: underline">{self.render_children(guild, bot)}</span>'

class StrikethroughNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f'<span style="text-decoration: line-through">{self.render_children(guild, bot)}</span>'

class SpoilerNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return (f'<span class="spoiler spoiler--hidden" onclick="showSpoiler(event, this)">'
                f'<span class="spoiler-text">{self.render_children(guild, bot)}</span></span>')

class InlineCodeNode(Node):
    def __init__(self, code: str):
        self.code = code
    def render(self, guild=None, bot=None):
        return f'<span class="pre pre-inline">{self.code}</span>'

class CodeBlockNode(Node):
    def __init__(self, lang: str, code: str):
        self.lang = lang
        self.code = code
    def render(self, guild=None, bot=None):
        lang_class = f"language-{self.lang}" if self.lang else "nohighlight"
        return f'<div class="pre pre--multiline {lang_class}">{self.code}</div>'

class QuoteNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f'<div class="quote"><div style="min-width: 0; flex: 1;">{self.render_children(guild, bot)}</div></div>'

class HeaderNode(ContainerNode):
    def __init__(self, level: int, children: List[Node]):
        super().__init__(children)
        self.level = level
    def render(self, guild=None, bot=None):
        return f"<h{self.level}>{self.render_children(guild, bot)}</h{self.level}>"

class SubtextNode(ContainerNode):
    def render(self, guild=None, bot=None):
        return f"<small>{self.render_children(guild, bot)}</small>"

class LinkNode(ContainerNode):
    def __init__(self, url: str, children: List[Node]):
        super().__init__(children)
        self.url = url
    def render(self, guild=None, bot=None):
        return f'<a href="{self.url}">{self.render_children(guild, bot)}</a>'

class HtmlNode(Node):
    def __init__(self, raw: str):
        self.raw = raw
    def render(self, guild=None, bot=None):
        return self.raw

class ListItemNode(ContainerNode):
    def __init__(self, indent_level: int, children: List[Node]):
        super().__init__(children)
        self.indent_level = indent_level
    def render(self, guild=None, bot=None):
        return f'<li class="markup">{self.render_children(guild, bot)}</li>'

class ListBlockNode(ContainerNode):
    def render(self, guild=None, bot=None):
        html = '<ul class="markup" style="padding-left: 20px;margin: 0 !important">\n'
        indent_stack = [0]
        
        for item in self.children:
            if not isinstance(item, ListItemNode):
                continue
            indent = item.indent_level
            if indent % 2 == 0:
                while indent < indent_stack[-1]:
                    html += "</ul>\n"
                    indent_stack.pop()
                if indent > indent_stack[-1]:
                    html += '<ul class="markup">\n'
                    indent_stack.append(indent)
            else:
                while indent + 1 < indent_stack[-1]:
                    html += "</ul>\n"
                    indent_stack.pop()
                if indent + 1 > indent_stack[-1]:
                    html += '<ul class="markup">\n'
                    indent_stack.append(indent + 1)
            html += item.render(guild, bot) + "\n"
            
        while len(indent_stack) > 1:
            html += "</ul>\n"
            indent_stack.pop()
        html += "</ul>"
        return html

class ChannelMentionNode(Node):
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
    def render(self, guild=None, bot=None):
        channel = guild.get_channel(self.channel_id) if guild else None
        if channel:
            return f'<span class="mention" title="{channel.id}">#{channel.name}</span>'
        return "#deleted-channel"

class UserMentionNode(Node):
    ESCAPE_LT, ESCAPE_GT, ESCAPE_AMP = "______lt______", "______gt______", "______amp______"
    def __init__(self, user_id: int):
        self.user_id = user_id
    def render(self, guild=None, bot=None):
        member = guild.get_member(self.user_id) if guild else None
        if not member and bot:
            member = bot.get_user(self.user_id)
        if member:
            escaped_name = member.display_name.replace("<", self.ESCAPE_LT).replace(">", self.ESCAPE_GT).replace("&", self.ESCAPE_AMP)
            return f'<span class="mention" title="{self.user_id}">@{escaped_name}</span>'
        return f'<span class="mention" title="{self.user_id}">&lt;@{self.user_id}&gt;</span>'

class RoleMentionNode(Node):
    def __init__(self, role_id: int):
        self.role_id = role_id
    def render(self, guild=None, bot=None):
        role = guild.get_role(self.role_id) if guild else None
        if role is None:
            return "@deleted-role"
        
        colour = "#dee0fc" if role.color.r == 0 and role.color.g == 0 and role.color.b == 0 else "#%02x%02x%02x" % (role.color.r, role.color.g, role.color.b)
        return f'<span style="color: {colour};">@{role.name}</span>'

class EveryoneMentionNode(Node):
    def render(self, guild=None, bot=None):
        return '<span class="mention" title="everyone">@everyone</span>'

class HereMentionNode(Node):
    def render(self, guild=None, bot=None):
        return '<span class="mention" title="here">@here</span>'

class SlashCommandNode(Node):
    def __init__(self, name: str):
        self.name = name
    def render(self, guild=None, bot=None):
        return f'<span class="mention" title="{self.name}">/{self.name}</span>'

class TimeMentionNode(Node):
    CYCLE_SECONDS = 12_622_780_800
    def __init__(self, timestamp: int, format_str: str, original: str):
        self.timestamp = timestamp
        self.format_str = format_str
        self.original = original
        
    def render(self, guild=None, bot=None):
        try:
            time_stamp = time.gmtime(self.timestamp - 1)
            datetime_stamp = datetime.datetime(2010, *time_stamp[1:6], tzinfo=pytz.utc)
            ui_time = datetime_stamp.strftime(self.format_str).replace(str(datetime_stamp.year), str(time_stamp[0]))
            tooltip_time = datetime_stamp.strftime("%A, %e %B %Y at %H:%M").replace(str(datetime_stamp.year), str(time_stamp[0]))
        except (OSError, OverflowError, ValueError):
            safe_ts = (self.timestamp - 1) % self.CYCLE_SECONDS
            years_shifted = ((self.timestamp - 1) // self.CYCLE_SECONDS) * 400
            dt = datetime.datetime.fromtimestamp(safe_ts, pytz.utc)
            final_year = dt.year + years_shifted
            ui_time = dt.strftime(self.format_str).replace(str(dt.year), str(final_year))
            tooltip_time = dt.strftime("%A, %e %B %Y at %H:%M").replace(str(dt.year), str(final_year))
            
        escaped_content = self.original.replace("<", "&lt;").replace(">", "&gt;")
        return f'<span class="unix-timestamp" data-timestamp="{tooltip_time}" raw-content="{escaped_content}">{ui_time}</span>'

class AstParser:
    """Parses raw text containing Discord markdown into an Abstract Syntax Tree (AST)."""
    def parse(self, text: str) -> List[Node]:
        if not text:
            return []
        nodes = self._parse_inline(str(text))
        nodes = self._merge_text_nodes(nodes)
        nodes = self._merge_quote_nodes(nodes)
        nodes = self._merge_list_nodes(nodes)
        return nodes

    def _parse_inline(self, text: str) -> List[Node]:
        nodes = []
        i, n = 0, len(text)
        
        while i < n:
            # Handle Discord tags and Mentions
            if text[i] == "<" or text[i:i+4] == "&lt;":
                is_escaped = text[i] == "&"
                
                # Channels
                if chan_match := re.match(r"&lt;#([0-9]+)&gt;" if is_escaped else r"<#([0-9]+)>", text[i:]):
                    nodes.append(ChannelMentionNode(int(chan_match.group(1))))
                    i += len(chan_match.group(0))
                    continue
                
                # Roles
                if role_match := re.match(r"&lt;@&amp;([0-9]+)&gt;" if is_escaped else r"<@&([0-9]+)>", text[i:]):
                    nodes.append(RoleMentionNode(int(role_match.group(1))))
                    i += len(role_match.group(0))
                    continue
                
                # Users
                if mem_match := re.match(r"&lt;@!?([0-9]+)&gt;" if is_escaped else r"<@!?([0-9]+)>", text[i:]):
                    nodes.append(UserMentionNode(int(mem_match.group(1))))
                    i += len(mem_match.group(0))
                    continue
                
                # Slash Commands
                if slash_match := re.match(r"&lt;\/([\w]+ ?[\w]*):[0-9]+&gt;" if is_escaped else r"<\/([\w]+ ?[\w]*):[0-9]+>", text[i:]):
                    nodes.append(SlashCommandNode(slash_match.group(1)))
                    i += len(slash_match.group(0))
                    continue
                
                # Timestamps
                time_patterns = (
                    [[r"&lt;t:([0-9]{1,13}):t&gt;", "%H:%M"], [r"&lt;t:([0-9]{1,13}):T&gt;", "%T"], [r"&lt;t:([0-9]{1,13}):d&gt;", "%d/%m/%Y"], [r"&lt;t:([0-9]{1,13}):D&gt;", "%e %B %Y"], [r"&lt;t:([0-9]{1,13}):f&gt;", "%e %B %Y %H:%M"], [r"&lt;t:([0-9]{1,13}):F&gt;", "%A, %e %B %Y %H:%M"], [r"&lt;t:([0-9]{1,13}):R&gt;", "%e %B %Y %H:%M"], [r"&lt;t:([0-9]{1,13})&gt;", "%e %B %Y %H:%M"]] 
                    if is_escaped else 
                    [[r"<t:([0-9]{1,13}):t>", "%H:%M"], [r"<t:([0-9]{1,13}):T>", "%T"], [r"<t:([0-9]{1,13}):d>", "%d/%m/%Y"], [r"<t:([0-9]{1,13}):D>", "%e %B %Y"], [r"<t:([0-9]{1,13}):f>", "%e %B %Y %H:%M"], [r"<t:([0-9]{1,13}):F>", "%A, %e %B %Y %H:%M"], [r"<t:([0-9]{1,13}):R>", "%e %B %Y %H:%M"], [r"<t:([0-9]{1,13})>", "%e %B %Y %H:%M"]]
                )
                time_found = False
                for pattern, strf in time_patterns:
                    if t_match := re.match(pattern, text[i:]):
                        nodes.append(TimeMentionNode(int(t_match.group(1)), strf, t_match.group(0)))
                        i += len(t_match.group(0))
                        time_found = True
                        break
                if time_found:
                    continue
                
                # Raw HTML
                if text[i] == "<":
                    if tag_match := re.match(r"(<[^>]+>)", text[i:]):
                        nodes.append(HtmlNode(tag_match.group(1)))
                        i += len(tag_match.group(1))
                        continue

            # Text Formatting
            if text[i] == "\n":
                nodes.append(TextNode("\n"))
                i += 1
                continue
            
            if text[i:i+3] == "```":
                if (endtag := text.find("```", i+3)) != -1:
                    inner = text[i+3:endtag]
                    lines = inner.split("\n", 1)
                    lang, code = (lines[0], lines[1]) if len(lines) > 1 and " " not in lines[0] else ("", inner)
                    
                    if code.startswith("\n"): code = code[1:]
                    if code.endswith("\n"): code = code[:-1]
                    
                    nodes.append(CodeBlockNode(lang, code))
                    i = endtag + 3
                    continue
            
            if text[i:i+2] == "``":
                if (endtag := text.find("``", i+2)) != -1:
                    nodes.append(InlineCodeNode(text[i+2:endtag]))
                    i = endtag + 2
                    continue
                    
            if text[i] == "`":
                if (endtag := text.find("`", i+1)) != -1:
                    nodes.append(InlineCodeNode(text[i+1:endtag]))
                    i = endtag + 1
                    continue

            if text[i:i+2] == "**":
                if (endtag := text.find("**", i+2)) != -1:
                    nodes.append(BoldNode(self._parse_inline(text[i+2:endtag])))
                    i = endtag + 2
                    continue
                    
            if text[i:i+2] == "__":
                if (endtag := text.find("__", i+2)) != -1:
                    nodes.append(UnderlineNode(self._parse_inline(text[i+2:endtag])))
                    i = endtag + 2
                    continue
                    
            if text[i:i+2] == "~~":
                if (endtag := text.find("~~", i+2)) != -1:
                    nodes.append(StrikethroughNode(self._parse_inline(text[i+2:endtag])))
                    i = endtag + 2
                    continue
                    
            if text[i:i+2] == "||":
                if (endtag := text.find("||", i+2)) != -1:
                    nodes.append(SpoilerNode(self._parse_inline(text[i+2:endtag])))
                    i = endtag + 2
                    continue

            if text[i] == "*":
                if (endtag := text.find("*", i+1)) != -1 and text[i:i+2] != "**":
                    nodes.append(ItalicNode(self._parse_inline(text[i+1:endtag])))
                    i = endtag + 1
                    continue
                    
            if text[i] == "_":
                if (endtag := text.find("_", i+1)) != -1 and text[i:i+2] != "__":
                    nodes.append(ItalicNode(self._parse_inline(text[i+1:endtag])))
                    i = endtag + 1
                    continue

            # Standard Mentions
            if text[i] == "@":
                if re.match(r"@(everyone)(?:[$\s\t\n\f\r\0]|$)", text[i:]):
                    nodes.append(EveryoneMentionNode())
                    i += 9
                    continue
                if re.match(r"@(here)(?:[$\s\t\n\f\r\0]|$)", text[i:]):
                    nodes.append(HereMentionNode())
                    i += 5
                    continue

            # Headers and Subtext
            if (i == 0 or text[i-1] == "\n") and text[i] == "#":
                if level_match := re.match(r"^(#{1,3})\s+", text[i:]):
                    prefix_len = len(level_match.group(0))
                    endtag = text.find("\n", i + prefix_len)
                    
                    nodes.append(HeaderNode(
                        len(level_match.group(1)),
                        self._parse_inline(text[i + prefix_len : endtag if endtag != -1 else n])
                    ))
                    
                    if endtag != -1:
                        i = endtag + 1
                        while i < n and text[i] == "\n":
                            i += 1
                    else:
                        break
                    continue

            if (i == 0 or text[i-1] == "\n") and text[i:i+3] == "-# ":
                endtag = text.find("\n", i+3)
                nodes.append(SubtextNode(self._parse_inline(text[i+3 : endtag if endtag != -1 else n])))
                if endtag != -1:
                    nodes.append(TextNode("\n"))
                    i = endtag + 1
                else:
                    break
                continue

            # Blockquotes
            if (i == 0 or text[i-1] == "\n") and (text[i:i+13] == "&gt;&gt;&gt; " or text[i:i+12] == "&gt;&gt;&gt;"):
                prefix_len = 13 if text[i:i+13] == "&gt;&gt;&gt; " else 12
                if text[i+prefix_len:i+prefix_len+4] != "&gt;":
                    nodes.append(QuoteNode(self._parse_inline(text[i+prefix_len:])))
                    break

            if (i == 0 or text[i-1] == "\n") and text[i:i+5] == "&gt; ":
                endtag = text.find("\n", i+5)
                nodes.append(QuoteNode(self._parse_inline(text[i+5 : endtag if endtag != -1 else n])))
                if endtag != -1:
                    i = endtag + 1
                    continue
                else:
                    break

            # Lists
            if i == 0 or text[i-1] == "\n":
                if list_match := re.match(r"^(\s*)([-*])\s+", text[i:]):
                    prefix_len = len(list_match.group(0))
                    endtag = text.find("\n", i + prefix_len)
                    
                    nodes.append(ListItemNode(
                        len(list_match.group(1)),
                        self._parse_inline(text[i + prefix_len : endtag if endtag != -1 else n])
                    ))
                    
                    if endtag != -1:
                        i = endtag + 1
                    else:
                        break
                    continue

            # Hyperlinks
            if text[i] == "[":
                if (close_bracket := text.find("](", i+1)) != -1 and (end_paren := text.find(")", close_bracket+2)) != -1:
                    nodes.append(LinkNode(text[close_bracket+2:end_paren], self._parse_inline(text[i+1:close_bracket])))
                    i = end_paren + 1
                    continue

            # Raw Links
            if text[i:i+4] == "http":
                if match := re.search(r"^https?://[^\s<*\n\)]+", text[i:]):
                    url = match.group(0)
                    nodes.append(LinkNode(url, [TextNode(url)]))
                    i += len(url)
                    continue

            # Fast-forward to next special character
            valid_specials = [pos for pos in [text.find(c, i+1) for c in "<`*_~|[\n&#-@"] if pos != -1]
            next_special = min(valid_specials) if valid_specials else n
            
            if next_special == i:
                nodes.append(TextNode(text[i]))
                i += 1
            else:
                nodes.append(TextNode(text[i:next_special]))
                i = next_special
                
        return nodes

    def _merge_text_nodes(self, nodes: List[Node]) -> List[Node]:
        merged = []
        for node in nodes:
            if isinstance(node, TextNode):
                node.text = node.text.replace("\n", "<br>")
                if merged and isinstance(merged[-1], TextNode):
                    merged[-1].text += node.text
                else:
                    merged.append(node)
            else:
                if isinstance(node, ContainerNode):
                    node.children = self._merge_text_nodes(node.children)
                merged.append(node)
        return merged

    def _merge_quote_nodes(self, nodes: List[Node]) -> List[Node]:
        merged, pending_spaces = [], []
        for node in nodes:
            if isinstance(node, ContainerNode):
                node.children = self._merge_quote_nodes(node.children)
                
            if isinstance(node, QuoteNode):
                pending_spaces.clear()
                if merged and isinstance(merged[-1], QuoteNode):
                    merged[-1].children.append(TextNode("<br>"))
                    merged[-1].children.extend(node.children)
                else:
                    merged.append(node)
            elif merged and isinstance(merged[-1], QuoteNode) and isinstance(node, TextNode) and not node.text.replace("<br>", "").strip():
                pending_spaces.append(node)
            else:
                if pending_spaces:
                    merged.extend(pending_spaces)
                    pending_spaces.clear()
                merged.append(node)
                
        if pending_spaces:
            merged.extend(pending_spaces)
        return merged

    def _merge_list_nodes(self, nodes: List[Node]) -> List[Node]:
        merged, current_list, pending_spaces = [], [], []
        
        def commit_list():
            if current_list:
                merged.append(ListBlockNode(current_list.copy()))
                current_list.clear()
            if pending_spaces:
                merged.extend(pending_spaces)
                pending_spaces.clear()
                
        for node in nodes:
            if isinstance(node, ContainerNode):
                node.children = self._merge_list_nodes(node.children)
                
            if isinstance(node, ListItemNode):
                pending_spaces.clear()
                current_list.append(node)
            elif current_list and isinstance(node, TextNode) and not node.text.replace("<br>", "").strip():
                pending_spaces.append(node)
            else:
                commit_list()
                merged.append(node)
                
        commit_list()
        return merged

# =========================================================================
# 6. HTML & COMPONENT GENERATION (Parsing layer wrappers)
# =========================================================================
bot: Optional[discord.Client] = None
def pass_bot(_bot):
    global bot
    bot = _bot

class ParseMarkdown:
    """A wrapper for AstParser handling standard text replacement blocks."""
    def __init__(self, content, guild=None, _bot=None):
        self.content = content
        self.guild = guild
        self.bot = _bot or bot
        self.code_blocks = []

    def parse_code_block_markdown(self):
        def repl_multiline(match):
            self.code_blocks.append(match.group(0))
            return f"{{{{CODE_BLOCK_{len(self.code_blocks) - 1}}}}}"
        self.content = re.sub(r"```.*?```", repl_multiline, self.content, flags=re.DOTALL)
        
        def repl_inline(match):
            self.code_blocks.append(match.group(0))
            return f"{{{{CODE_BLOCK_{len(self.code_blocks) - 1}}}}}"
        self.content = re.sub(r"`.*?`", repl_inline, self.content, flags=re.DOTALL)

    def reverse_code_block_markdown(self):
        for i, block in enumerate(self.code_blocks):
            self.content = self.content.replace(f"{{{{CODE_BLOCK_{i}}}}}", block)

    async def standard_message_flow(self):
        self.content = "".join(n.render(self.guild, self.bot) for n in AstParser().parse(self.content))
        await self.parse_emoji()
        return self.content

    async def link_embed_flow(self): return await self.standard_message_flow()
    async def standard_embed_flow(self): return await self.standard_message_flow()
    async def special_embed_flow(self): return await self.standard_embed_flow()
    
    async def message_reference_flow(self):
        self.strip_preserve()
        return await self.standard_embed_flow()

    async def special_emoji_flow(self):
        await self.parse_emoji()
        return self.content

    def strip_preserve(self):
        self.content = re.sub(r'<span class="chatlog__markdown-preserve">(.*?)</span>', r"\1", self.content)

    async def parse_emoji(self):
        holder = (
            [r"&lt;:.*?:(\d*)&gt;", '<img class="emoji emoji--small" src="https://cdn.discordapp.com/emojis/%s.png">'],
            [r"&lt;a:.*?:(\d*)&gt;", '<img class="emoji emoji--small" src="https://cdn.discordapp.com/emojis/%s.gif">'],
            [r"<:.*?:(\d*)>", '<img class="emoji emoji--small" src="https://cdn.discordapp.com/emojis/%s.png">'],
            [r"<a:.*?:(\d*)>", '<img class="emoji emoji--small" src="https://cdn.discordapp.com/emojis/%s.gif">']
        )
        shield_blocks = []
        def repl(match):
            shield_blocks.append(match.group(0))
            return f"{{{{SHIELD_{len(shield_blocks) - 1}}}}}"
            
        self.content = re.sub(r'<div class="pre pre--multiline.*?</div>', repl, self.content, flags=re.DOTALL)
        self.content = re.sub(r'<span class="pre pre-inline">.*?</span>', repl, self.content, flags=re.DOTALL)
        self.content = await convert_emoji([word for word in self.content])
        
        for p, r in holder:
            def make_repl(template): return lambda match: template % match.group(1)
            self.content = re.sub(p, make_repl(r), self.content)
            
        for i, block in enumerate(shield_blocks):
            self.content = self.content.replace(f"{{{{SHIELD_{i}}}}}", block)

# Constants for Parsing Modes
PARSE_MODE_NONE = 0
PARSE_MODE_NO_MARKDOWN = 1
PARSE_MODE_MARKDOWN = 2
PARSE_MODE_EMBED = 3
PARSE_MODE_SPECIAL_EMBED = 4
PARSE_MODE_REFERENCE = 5
PARSE_MODE_EMOJI = 6
PARSE_MODE_HTML_SAFE = 7

async def fill_out(guild, base, replacements):
    """Fills out a base HTML string using replacement key-value pairs."""
    resolved = {}
    for r in replacements:
        k, v, mode = (r[0], r[1], PARSE_MODE_MARKDOWN) if len(r) == 2 else r
        
        if mode == PARSE_MODE_MARKDOWN:
            v = await ParseMarkdown(v, guild, bot).standard_message_flow()
        elif mode == PARSE_MODE_EMBED:
            v = await ParseMarkdown(v, guild, bot).standard_embed_flow()
        elif mode == PARSE_MODE_SPECIAL_EMBED:
            v = await ParseMarkdown(v, guild, bot).special_embed_flow()
        elif mode == PARSE_MODE_REFERENCE:
            v = await ParseMarkdown(v, guild, bot).message_reference_flow()
        elif mode == PARSE_MODE_EMOJI:
            v = await ParseMarkdown(v, guild, bot).special_emoji_flow()
        elif mode == PARSE_MODE_HTML_SAFE:
            if mode != PARSE_MODE_NONE:
                v = await ParseMarkdown(v, guild, bot).standard_embed_flow()
            v = json.dumps(html.escape(v, quote=True), ensure_ascii=False)[1:-1]
        elif mode != PARSE_MODE_NONE:
            v = await ParseMarkdown(v, guild, bot).standard_embed_flow()
            
        resolved[k] = str(v or "").strip()

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda match: resolved.get(match.group(1), match.group(0)), base)

# NOTE: The actual HTML templates usually live in external files. Since this is 
# combined into a single script, you must place an 'html' folder adjacent to this script
# populated with the DiscordChatExporter template files, or update these variables with raw strings.
dir_path = os.path.abspath(os.path.join((os.path.dirname(os.path.realpath(__file__))), ".."))

def read_file(filename):
    try:
        with open(filename, "r") as f:
            return f.read()
    except Exception:
        # Fallback string if file is missing (to prevent complete script failure during testing)
        return ""

start_message = read_file(dir_path + "/html/message/start.html")
bot_tag = read_file(dir_path + "/html/message/bot-tag.html")
bot_tag_verified = read_file(dir_path + "/html/message/bot-tag-verified.html")
message_content = read_file(dir_path + "/html/message/content.html")
message_reference = read_file(dir_path + "/html/message/reference.html")
message_interaction = read_file(dir_path + "/html/message/interaction.html")
message_pin = read_file(dir_path + "/html/message/pin.html")
message_thread = read_file(dir_path + "/html/message/thread.html")
message_thread_remove = read_file(dir_path + "/html/message/thread_remove.html")
message_thread_add = read_file(dir_path + "/html/message/thread_add.html")
message_reference_unknown = read_file(dir_path + "/html/message/reference_unknown.html")
message_forwarded = read_file(dir_path + "/html/message/forwarded.html")
message_body = read_file(dir_path + "/html/message/message.html")
end_message = read_file(dir_path + "/html/message/end.html")
meta_data_temp = read_file(dir_path + "/html/message/meta.html")
component_button = read_file(dir_path + "/html/component/component_button.html")
component_menu = read_file(dir_path + "/html/component/component_menu.html")
component_menu_options = read_file(dir_path + "/html/component/component_menu_options.html")
component_menu_options_emoji = read_file(dir_path + "/html/component/component_menu_options_emoji.html")
component_container = read_file(dir_path + "/html/component/component_container.html")
component_section = read_file(dir_path + "/html/component/component_section.html")
component_text_display = read_file(dir_path + "/html/component/component_text_display.html")
component_thumbnail = read_file(dir_path + "/html/component/component_thumbnail.html")
component_media_gallery = read_file(dir_path + "/html/component/component_media_gallery.html")
component_media_gallery_item = read_file(dir_path + "/html/component/component_media_gallery_item.html")
component_separator = read_file(dir_path + "/html/component/component_separator.html")
component_file = read_file(dir_path + "/html/component/component_file.html")
embed_body = read_file(dir_path + "/html/embed/body.html")
embed_title = read_file(dir_path + "/html/embed/title.html")
embed_description = read_file(dir_path + "/html/embed/description.html")
embed_field = read_file(dir_path + "/html/embed/field.html")
embed_field_inline = read_file(dir_path + "/html/embed/field-inline.html")
embed_footer = read_file(dir_path + "/html/embed/footer.html")
embed_footer_icon = read_file(dir_path + "/html/embed/footer_image.html")
embed_image = read_file(dir_path + "/html/embed/image.html")
embed_thumbnail = read_file(dir_path + "/html/embed/thumbnail.html")
embed_author = read_file(dir_path + "/html/embed/author.html")
embed_author_icon = read_file(dir_path + "/html/embed/author_icon.html")
reaction_emoji = read_file(dir_path + "/html/reaction/emoji.html")
custom_emoji = read_file(dir_path + "/html/reaction/custom_emoji.html")
img_attachment = read_file(dir_path + "/html/attachment/image.html")
img_grid = read_file(dir_path + "/html/attachment/image_grid.html")
img_grid_item = read_file(dir_path + "/html/attachment/image_grid_item.html")
msg_attachment = read_file(dir_path + "/html/attachment/message.html")
audio_attachment = read_file(dir_path + "/html/attachment/audio.html")
video_attachment = read_file(dir_path + "/html/attachment/video.html")
total = read_file(dir_path + "/html/base.html")
fancy_time = read_file(dir_path + "/html/script/fancy_time.html")
channel_topic = read_file(dir_path + "/html/script/channel_topic.html")
channel_subject = read_file(dir_path + "/html/script/channel_subject.html")

# =========================================================================
# 7. CHAT MESSAGE COMPONENTS (Embeds, Attachments, Media)
# =========================================================================
def _gather_checker():
    if discord.module not in ["nextcord", "disnake"] and hasattr(discord.Embed, "Empty"):
        return discord.Embed.Empty
    return None

class Embed:
    """Builds the HTML structure for a single Discord Embed object."""
    def __init__(self, embed, guild, pytz_timezone=None, military_time=True):
        self.embed = embed
        self.guild = guild
        self.pytz_timezone = pytz_timezone
        self.military_time = military_time
        self.check_against = None

    async def flow(self):
        self.check_against = _gather_checker()
        self.r, self.g, self.b = (
            (self.embed.colour.r, self.embed.colour.g, self.embed.colour.b) 
            if self.embed.colour != self.check_against else (0x4A, 0x4A, 0x50)
        )
        await self.build_title()
        await self.build_description()
        await self.build_fields()
        await self.build_author()
        await self.build_image()
        await self.build_thumbnail()
        await self.build_footer()
        await self.build_embed()
        return self.embed

    def _format_embed_timestamp(self) -> str:
        if not getattr(self.embed, "timestamp", None) or self.embed.timestamp == self.check_against:
            return ""
        local_time = (self.embed.timestamp if getattr(self.embed.timestamp, "tzinfo", None) 
                      else timezone("UTC").localize(self.embed.timestamp)).astimezone(
                          timezone(self.pytz_timezone or getattr(self.guild, "timezone", "UTC") or "UTC"))
        return local_time.strftime("%d-%m-%Y %H:%M" if self.military_time else "%d-%m-%Y %I:%M %p")

    async def build_title(self):
        raw_title = html.escape(self.embed.title) if self.embed.title != self.check_against else ""
        if not raw_title:
            self.title = ""
            return
            
        title_html = await fill_out(self.guild, "{{EMBED_TITLE}}", [("EMBED_TITLE", raw_title, PARSE_MODE_MARKDOWN)])
        url_value = getattr(self.embed, "url", self.check_against)
        if url_value and url_value != self.check_against:
            title_html = f'<a href="{html.escape(str(url_value), quote=True)}">{title_html}</a>'
            
        self.title = await fill_out(self.guild, embed_title, [("EMBED_TITLE", title_html, PARSE_MODE_NONE)])

    async def build_description(self):
        escaped_desc = html.escape(self.embed.description) if self.embed.description != self.check_against else ""
        self.description = await fill_out(self.guild, embed_description, [("EMBED_DESC", escaped_desc, PARSE_MODE_EMBED)]) if escaped_desc else ""

    async def build_fields(self):
        self.fields = ""
        if not self.embed.fields:
            return
            
        rows, current_row = [], []
        for field in self.embed.fields:
            if not getattr(field, "inline", False):
                if current_row:
                    rows.append(current_row)
                    current_row = []
                rows.append([field])
            else:
                current_row.append(field)
                if len(current_row) == 3:
                    rows.append(current_row)
                    current_row = []
                    
        if current_row:
            rows.append(current_row)
            
        for row in rows:
            if len(row) == 1 and not getattr(row[0], "inline", False):
                field = row[0]
                field.name = html.escape(field.name)
                field.value = html.escape(field.value)
                self.fields += await fill_out(self.guild, embed_field, [("FIELD_NAME", field.name, PARSE_MODE_SPECIAL_EMBED), ("FIELD_VALUE", field.value, PARSE_MODE_EMBED), ("GRID_COLUMN", "1 / 13", PARSE_MODE_NONE)])
            else:
                cols = ["1 / 5", "5 / 9", "9 / 13"] if len(row) == 3 else ["1 / 7", "7 / 13"] if len(row) == 2 else ["1 / 13"]
                for idx, field in enumerate(row):
                    field.name = html.escape(field.name)
                    field.value = html.escape(field.value)
                    self.fields += await fill_out(self.guild, embed_field_inline, [("FIELD_NAME", field.name, PARSE_MODE_SPECIAL_EMBED), ("FIELD_VALUE", field.value, PARSE_MODE_EMBED), ("GRID_COLUMN", cols[idx], PARSE_MODE_NONE)])

    async def build_author(self):
        self.author = html.escape(self.embed.author.name) if (self.embed.author and self.embed.author.name != self.check_against) else ""
        self.author = f'<a class="chatlog__embed-author-name-link" href="{self.embed.author.url}">{self.author}</a>' if (self.embed.author and self.embed.author.url != self.check_against) else self.author
        
        author_icon = await fill_out(self.guild, embed_author_icon, [("AUTHOR", self.author, PARSE_MODE_NONE), ("AUTHOR_ICON", self.embed.author.icon_url, PARSE_MODE_NONE)]) if self.embed.author and self.embed.author.icon_url != self.check_against else ""
        self.author = await fill_out(self.guild, embed_author, [("AUTHOR", self.author, PARSE_MODE_NONE)]) if author_icon == "" and self.author != "" else author_icon

    async def build_image(self):
        self.image = await fill_out(self.guild, embed_image, [("EMBED_IMAGE", str(self.embed.image.url), PARSE_MODE_NONE)]) if self.embed.image and self.embed.image.url != self.check_against else ""

    async def build_thumbnail(self):
        self.thumbnail = await fill_out(self.guild, embed_thumbnail, [("EMBED_THUMBNAIL", str(self.embed.thumbnail.url), PARSE_MODE_NONE)]) if self.embed.thumbnail and self.embed.thumbnail.url != self.check_against else ""

    async def build_footer(self):
        footer_text = html.escape(self.embed.footer.text) if (self.embed.footer and self.embed.footer.text != self.check_against) else ""
        footer_icon = self.embed.footer.icon_url if (self.embed.footer and self.embed.footer.icon_url != self.check_against) else None
        timestamp_text = self._format_embed_timestamp()
        
        if footer_text and timestamp_text:
            footer_text = f"{footer_text} | {timestamp_text}"
        elif not footer_text and timestamp_text:
            footer_text = timestamp_text
            
        if not footer_text:
            self.footer = ""
            return
            
        if footer_icon is not None:
            self.footer = await fill_out(self.guild, embed_footer_icon, [("EMBED_FOOTER", footer_text, PARSE_MODE_NONE), ("EMBED_FOOTER_ICON", footer_icon, PARSE_MODE_NONE)])
        else:
            self.footer = await fill_out(self.guild, embed_footer, [("EMBED_FOOTER", footer_text, PARSE_MODE_NONE)])

    async def build_embed(self):
        self.embed = await fill_out(self.guild, embed_body, [
            ("EMBED_R", str(self.r)), ("EMBED_G", str(self.g)), ("EMBED_B", str(self.b)),
            ("EMBED_AUTHOR", self.author, PARSE_MODE_NONE), ("EMBED_TITLE", self.title, PARSE_MODE_NONE),
            ("EMBED_IMAGE", self.image, PARSE_MODE_NONE), ("EMBED_THUMBNAIL", self.thumbnail, PARSE_MODE_NONE),
            ("EMBED_DESC", self.description, PARSE_MODE_NONE), ("EMBED_FIELDS", self.fields, PARSE_MODE_NONE),
            ("EMBED_FOOTER", self.footer, PARSE_MODE_NONE)
        ])

class Reaction:
    """Builds the HTML structure for message reactions (custom or unicode)."""
    def __init__(self, reaction, guild):
        self.reaction = reaction
        self.guild = guild

    async def flow(self):
        if ":" in str(self.reaction.emoji):
            emoji_id = re.search(r":.*:(\d*)", str(self.reaction.emoji)).group(1)
            is_gif = bool(re.compile(r"&lt;a:.*:.*&gt;").search(str(self.reaction.emoji)))
            self.reaction = await fill_out(self.guild, custom_emoji, [
                ("EMOJI", str(emoji_id), PARSE_MODE_NONE),
                ("EMOJI_COUNT", str(self.reaction.count), PARSE_MODE_NONE),
                ("EMOJI_FILE", "gif" if is_gif else "png", PARSE_MODE_NONE)
            ])
        else:
            self.reaction = await fill_out(self.guild, reaction_emoji, [
                ("EMOJI", str(await convert_emoji(self.reaction.emoji)), PARSE_MODE_NONE),
                ("EMOJI_COUNT", str(self.reaction.count), PARSE_MODE_NONE)
            ])
        return self.reaction

class Attachment:
    """Handles parsing and generating HTML for message attachments (Images, Videos, Audio, Files)."""
    def __init__(self, attachments, guild):
        self.attachments = attachments
        self.guild = guild

    async def flow(self):
        is_spoiler = self._is_spoiler()
        
        if self.attachments.content_type is not None:
            if "image" in self.attachments.content_type:
                self.attachments = await fill_out(self.guild, img_attachment, [("ATTACH_URL", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_URL_THUMB", self.attachments.url, PARSE_MODE_NONE)])
            elif "video" in self.attachments.content_type:
                width_str = f'width="{self.attachments.width}"' if getattr(self.attachments, "width", None) else ""
                height_str = f'height="{self.attachments.height}"' if getattr(self.attachments, "height", None) else ""
                self.attachments = await fill_out(self.guild, video_attachment, [("ATTACH_URL", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_WIDTH", width_str, PARSE_MODE_NONE), ("ATTACH_HEIGHT", height_str, PARSE_MODE_NONE)])
            elif "audio" in self.attachments.content_type:
                self.attachments = await fill_out(self.guild, audio_attachment, [("ATTACH_ICON", DiscordUtils.file_attachment_audio, PARSE_MODE_NONE), ("ATTACH_URL", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_BYTES", str(self.get_file_size(self.attachments.size)), PARSE_MODE_NONE), ("ATTACH_AUDIO", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_FILE", str(self.attachments.filename), PARSE_MODE_NONE)])
            else:
                self.attachments = await fill_out(self.guild, msg_attachment, [("ATTACH_ICON", await self.get_file_icon(), PARSE_MODE_NONE), ("ATTACH_URL", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_BYTES", str(self.get_file_size(self.attachments.size)), PARSE_MODE_NONE), ("ATTACH_FILE", str(self.attachments.filename), PARSE_MODE_NONE)])
        else:
            self.attachments = await fill_out(self.guild, msg_attachment, [("ATTACH_ICON", await self.get_file_icon(), PARSE_MODE_NONE), ("ATTACH_URL", self.attachments.url, PARSE_MODE_NONE), ("ATTACH_BYTES", str(self.get_file_size(self.attachments.size)), PARSE_MODE_NONE), ("ATTACH_FILE", str(self.attachments.filename), PARSE_MODE_NONE)])
            
        if is_spoiler and isinstance(self.attachments, str):
            replacements = (
                ("<div class='chatlog__attachment'>", "<div class='chatlog__attachment chatlog__attachment-spoiler'>"),
                ("<div class=\"chatlog__attachment\">", "<div class=\"chatlog__attachment chatlog__attachment-spoiler\">"),
                ("<div class=chatlog__attachment>", "<div class=\"chatlog__attachment chatlog__attachment-spoiler\">"),
                ("class='chatlog__attachment'", "class='chatlog__attachment chatlog__attachment-spoiler'"),
                ("class=\"chatlog__attachment\"", "class=\"chatlog__attachment chatlog__attachment-spoiler\""),
                ("class=chatlog__attachment", "class=\"chatlog__attachment chatlog__attachment-spoiler\"")
            )
            for target, replacement in replacements:
                if target in self.attachments:
                    self.attachments = self.attachments.replace(target, replacement, 1)
                    break
        return self.attachments

    @staticmethod
    def get_file_size(file_size):
        if file_size == 0:
            return "0 bytes"
        i = int(math.floor(math.log(file_size, 1024)))
        return "%s %s" % (round(file_size / math.pow(1024, i), 2), ("bytes", "KB", "MB")[i])

    async def get_file_icon(self) -> str:
        return self.resolve_file_icon(
            name=str(getattr(self.attachments, "filename", "") or ""),
            content_type=str(getattr(self.attachments, "content_type", "") or ""),
            url=str(getattr(self.attachments, "url", "") or "")
        )

    @staticmethod
    def resolve_file_icon(name: str = "", content_type: str = "", url: str = "") -> str:
        content_type = (content_type or "").lower()
        if content_type.startswith("audio/"): return DiscordUtils.file_attachment_audio
        
        extension = next((str(candidate).split("?", 1)[0].split("#", 1)[0].rsplit(".", 1)[-1].lower() 
                          for candidate in (name, url) if candidate and "." in str(candidate).split("?", 1)[0].split("#", 1)[0]), "")
                          
        if not extension and content_type:
            extension = "html" if "html" in content_type else "pdf" if "pdf" in content_type else ""
            
        if extension in ("pdf",): return DiscordUtils.file_attachment_acrobat
        elif extension in ("html", "htm", "css", "rss", "xhtml", "xml"): return DiscordUtils.file_attachment_webcode
        elif extension in ("py", "cgi", "pl", "gadget", "jar", "msi", "wsf", "bat", "php", "js"): return DiscordUtils.file_attachment_code
        elif extension in ("txt", "doc", "docx", "rtf", "xls", "xlsx", "ppt", "pptx", "odt", "odp", "ods", "odg", "odf", "swx", "sxi", "sxc", "sxd", "stw"): return DiscordUtils.file_attachment_document
        elif extension in ("br", "rpm", "dcm", "epub", "zip", "tar", "rar", "gz", "bz2", "7x", "7z", "deb", "ar", "z", "lzo", "lz", "lz4", "arj", "pkg"): return DiscordUtils.file_attachment_archive
        return DiscordUtils.file_attachment_unknown

    def _is_spoiler(self) -> bool:
        if callable(getattr(self.attachments, "spoiler", None)):
            try: return bool(getattr(self.attachments, "spoiler", None)())
            except Exception: pass
        if getattr(self.attachments, "spoiler", None) is not None:
            return bool(getattr(self.attachments, "spoiler", None))
        if callable(getattr(self.attachments, "is_spoiler", None)):
            try: return bool(getattr(self.attachments, "is_spoiler", None)())
            except Exception: return False
        return False

class AttachmentGrid:
    """Combines multiple media attachments into a structured CSS grid (for mosaics)."""
    def __init__(self, attachments, guild, attachmentCount):
        self.attachments = attachments
        self.guild = guild
        self.attachmentCount = attachmentCount

    async def flow(self):
        grid_items_html = "".join([await fill_out(self.guild, img_grid_item, [
            ("ITEM_CLASS", "", PARSE_MODE_NONE),
            ("ITEM_CONTENT", await Attachment(a, self.guild).flow(), PARSE_MODE_NONE)
        ]) for a in self.attachments])
        
        chunk_size = len(self.attachments)
        grid_class = (
            "chatlog__attachment-grid--single" if chunk_size == 1 and self.attachmentCount == 1 else
            "chatlog__attachment-grid--1x1" if chunk_size == 1 else
            "chatlog__attachment-grid--1x2" if chunk_size == 2 else
            "chatlog__attachment-grid--1x3" if chunk_size == 3 and self.attachmentCount == 3 else
            "chatlog__attachment-grid--3x3" if chunk_size == 3 else
            "chatlog__attachment-grid--2x2" if chunk_size == 4 else
            "chatlog__attachment-grid--3x3"
        )
        return await fill_out(self.guild, img_grid, [("GRID_CLASS", grid_class, PARSE_MODE_NONE), ("GRID_ITEMS", grid_items_html, PARSE_MODE_NONE)])

# =========================================================================
# 8. MESSAGE AND TRANSCRIPT GENERATOR
# =========================================================================
class MessageConstruct:
    """Builds the comprehensive HTML wrapper for a single message, combining attachments/embeds/text."""
    def __init__(self, message: discord.Message, previous_message: Optional[discord.Message], pytz_timezone, military_time: bool, guild: discord.Guild, meta_data: dict, message_dict: dict, attachment_handler):
        self.message = message
        self.previous_message = previous_message
        self.pytz_timezone = pytz_timezone
        self.military_time = military_time
        self.guild = guild
        self.message_dict = message_dict
        self.attachment_handler = attachment_handler
        self.time_format = "%A, %e %B %Y %H:%M" if self.military_time else "%A, %e %B %Y %I:%M %p"
        
        self.message_created_at, self.message_edited_at = self.set_time()
        self.meta_data = meta_data
        self.forwarded = False
        
        self.rendered_content = ""
        self.message_html, self.embeds, self.forwarded_embeds = "", "", ""
        self.reactions, self.components, self.attachments, self.interaction = "", "", "", ""

    def get_message_snapshots(self):
        return getattr(self.message, "message_snapshots", getattr(self.message, "snapshots", []))

    async def construct_message(self) -> (str, dict):
        if discord.MessageType.pins_add == self.message.type:
            await self.generate_message_divider(channel_audit=True)
            self.message_html += await fill_out(self.guild, message_pin, [("PIN_URL", DiscordUtils.pinned_message_icon, PARSE_MODE_NONE), ("USER_COLOUR", await self._gather_user_colour(self.message.author)), ("NAME", str(html.escape(self.message.author.display_name))), ("NAME_TAG", await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")), PARSE_MODE_NONE), ("MESSAGE_ID", str(self.message.id), PARSE_MODE_NONE), ("REF_MESSAGE_ID", str(self.message.reference.message_id) if getattr(self.message, "reference", None) else "", PARSE_MODE_NONE)])
        elif discord.MessageType.thread_created == self.message.type:
            await self.generate_message_divider(channel_audit=True)
            self.message_html += await fill_out(self.guild, message_thread, [("THREAD_URL", DiscordUtils.thread_channel_icon, PARSE_MODE_NONE), ("THREAD_NAME", self.message.content, PARSE_MODE_NONE), ("USER_COLOUR", await self._gather_user_colour(self.message.author)), ("NAME", str(html.escape(self.message.author.display_name))), ("NAME_TAG", await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")), PARSE_MODE_NONE), ("MESSAGE_ID", str(self.message.id), PARSE_MODE_NONE)])
        elif getattr(discord.MessageType, "recipient_remove", None) == self.message.type:
            await self.generate_message_divider(channel_audit=True)
            self.message_html += await fill_out(self.guild, message_thread_remove, [("THREAD_URL", DiscordUtils.thread_remove_recipient, PARSE_MODE_NONE), ("USER_COLOUR", await self._gather_user_colour(self.message.author)), ("NAME", str(html.escape(self.message.author.display_name))), ("NAME_TAG", await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")), PARSE_MODE_NONE), ("RECIPIENT_USER_COLOUR", await self._gather_user_colour(self.message.mentions[0])), ("RECIPIENT_NAME", str(html.escape(self.message.mentions[0].display_name))), ("RECIPIENT_NAME_TAG", await discriminator(self.message.mentions[0].name, getattr(self.message.mentions[0], "discriminator", "0")), PARSE_MODE_NONE), ("MESSAGE_ID", str(self.message.id), PARSE_MODE_NONE)])
        elif getattr(discord.MessageType, "recipient_add", None) == self.message.type:
            await self.generate_message_divider(channel_audit=True)
            self.message_html += await fill_out(self.guild, message_thread_add, [("THREAD_URL", DiscordUtils.thread_add_recipient, PARSE_MODE_NONE), ("USER_COLOUR", await self._gather_user_colour(self.message.author)), ("NAME", str(html.escape(self.message.author.display_name))), ("NAME_TAG", await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")), PARSE_MODE_NONE), ("RECIPIENT_USER_COLOUR", await self._gather_user_colour(self.message.mentions[0])), ("RECIPIENT_NAME", str(html.escape(self.message.mentions[0].display_name))), ("RECIPIENT_NAME_TAG", await discriminator(self.message.mentions[0].name, getattr(self.message.mentions[0], "discriminator", "0")), PARSE_MODE_NONE), ("MESSAGE_ID", str(self.message.id), PARSE_MODE_NONE)])
        else:
            await self.build_message()
        return self.message_html, self.meta_data

    async def build_message(self):
        await self.build_content()
        await self.build_reference()
        await self.build_interaction()
        await self.build_sticker()
        await self.build_assets()
        await self.wrap_forwarded()
        await self.build_message_template()
        await self.build_meta_data()

    async def build_meta_data(self):
        user_id = self.message.author.id
        if user_id in self.meta_data:
            self.meta_data[user_id][4] += 1
        else:
            self.meta_data[user_id] = [
                await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")),
                self.message.author.created_at,
                bot_tag_verified if self.message.author.bot and getattr(self.message.author.public_flags, "verified_bot", False) else bot_tag if self.message.author.bot else "",
                self.message.author.display_avatar if hasattr(self.message.author, "display_avatar") and self.message.author.display_avatar else DiscordUtils.default_avatar,
                1,
                getattr(self.message.author, "joined_at", None),
                f'<div class="meta__display-name">{self.message.author.display_name}</div>' if self.message.author.display_name != self.message.author.name else ""
            ]

    async def build_content(self):
        if not self.message.content and not self.get_message_snapshots():
            self.rendered_content = ""
            return
        if self.message_edited_at:
            self.message_edited_at = f'<span class="chatlog__reference-edited-timestamp" data-timestamp="{self.message_edited_at}">(edited)</span>'
            
        snapshots = self.get_message_snapshots()
        if snapshots:
            combined = f"{self.message.content} {' '.join(s.content for s in snapshots if hasattr(s, 'content'))}"
            self.forwarded = True
        else:
            combined = self.message.content
            
        self.rendered_content = await fill_out(self.guild, message_content, [("MESSAGE_CONTENT", html.escape(combined or ""), PARSE_MODE_MARKDOWN), ("EDIT", self.message_edited_at, PARSE_MODE_NONE)])

    async def build_reference(self):
        if not getattr(self.message, "reference", None):
            self.message.reference = ""
            return
            
        message = self.message_dict.get(self.message.reference.message_id)
        if not message:
            try:
                message = await self.message.channel.fetch_message(self.message.reference.message_id)
            except (discord.NotFound, discord.HTTPException) as e:
                self.message.reference = ""
                if self.forwarded: return
                if isinstance(e, discord.NotFound): self.message.reference = message_reference_unknown
                return
                
        interaction_status = getattr(message, "interaction_metadata", getattr(message, "interaction", None))
        icon, dummy = (DiscordUtils.reference_attachment_icon, "Click to see attachment") if not interaction_status and (message.embeds or message.attachments) else (DiscordUtils.interaction_command_icon, "Click to see command") if interaction_status else ("", "")
        
        if not getattr(message, "content", None):
            message.content = dummy
            
        _, message_edited_at = self.set_time(message)
        self.message.reference = await fill_out(self.guild, message_reference, [
            ("AVATAR_URL", str(message.author.display_avatar if hasattr(message.author, "display_avatar") and message.author.display_avatar else DiscordUtils.default_avatar), PARSE_MODE_NONE),
            ("BOT_TAG", bot_tag_verified if message.author.bot and getattr(message.author.public_flags, "verified_bot", False) else bot_tag if message.author.bot else "", PARSE_MODE_NONE),
            ("NAME_TAG", await discriminator(message.author.name, getattr(message.author, "discriminator", "0")), PARSE_MODE_NONE),
            ("NAME", str(html.escape(message.author.display_name))),
            ("USER_COLOUR", await self._gather_user_colour(message.author), PARSE_MODE_NONE),
            ("CONTENT", message.content.replace("\n", "").replace("<br>", ""), PARSE_MODE_REFERENCE),
            ("EDIT", f'<span class="chatlog__reference-edited-timestamp" data-timestamp="{message_edited_at}">(edited)</span>' if message_edited_at else "", PARSE_MODE_NONE),
            ("ICON", icon, PARSE_MODE_NONE),
            ("USER_ID", str(message.author.id), PARSE_MODE_NONE),
            ("MESSAGE_ID", str(self.message.reference.message_id), PARSE_MODE_NONE)
        ])

    async def build_interaction(self):
        if hasattr(self.message, "interaction_metadata") and self.message.interaction_metadata:
            command, user, interaction_id = "a slash command", self.message.interaction_metadata.user, self.message.interaction_metadata.id
        elif getattr(self.message, "interaction", None):
            command, user, interaction_id = f"/{self.message.interaction.name}", self.message.interaction.user, self.message.interaction.id
        else:
            self.interaction = ""
            return
            
        self.interaction = await fill_out(self.guild, message_interaction, [
            ("AVATAR_URL", str(user.display_avatar if hasattr(user, "display_avatar") and user.display_avatar else DiscordUtils.default_avatar), PARSE_MODE_NONE),
            ("BOT_TAG", bot_tag_verified if user.bot and getattr(user.public_flags, "verified_bot", False) else bot_tag if user.bot else "", PARSE_MODE_NONE),
            ("NAME_TAG", await discriminator(user.name, getattr(user, "discriminator", "0")), PARSE_MODE_NONE),
            ("NAME", str(html.escape(user.display_name))),
            ("COMMAND", str(command), PARSE_MODE_NONE),
            ("USER_COLOUR", await self._gather_user_colour(user), PARSE_MODE_NONE),
            ("FILLER", "used ", PARSE_MODE_NONE),
            ("USER_ID", str(user.id), PARSE_MODE_NONE),
            ("INTERACTION_ID", str(interaction_id), PARSE_MODE_NONE)
        ])

    async def build_sticker(self):
        sticker_image_url = self.message.stickers[0].url if self.message.stickers and hasattr(self.message.stickers[0], "url") else next((snapshot.stickers[0].url for snapshot in self.get_message_snapshots() if hasattr(snapshot, "stickers") and snapshot.stickers and hasattr(snapshot.stickers[0], "url")), None)
        if not sticker_image_url:
            return
            
        if sticker_image_url.endswith(".json"):
            try:
                sticker = await self.message.stickers[0].fetch()
            except Exception:
                sticker = next((await snapshot.stickers[0].fetch() for snapshot in self.get_message_snapshots() if hasattr(snapshot, "stickers") and snapshot.stickers and hasattr(snapshot.stickers[0], "url")), None)
            sticker_image_url = f"https://cdn.jsdelivr.net/gh/mahtoid/DiscordUtils@master/stickers/{sticker.pack_id}/{sticker.id}.gif"
            
        self.rendered_content = await fill_out(self.guild, img_attachment, [("ATTACH_URL", str(sticker_image_url), PARSE_MODE_NONE), ("ATTACH_URL_THUMB", str(sticker_image_url), PARSE_MODE_NONE)])

    async def build_assets(self):
        processed_attachments, attachment_urls, media_group = [], set(), []
        
        for a in self.message.attachments:
            if self.attachment_handler:
                a = await self.attachment_handler.process_asset(a)
            processed_attachments.append(a)
            attachment_urls.update([str(v) for attr in ("url", "proxy_url") if (v := getattr(a, attr, None))])
            
        for e in self.message.embeds:
            if attachment_urls and getattr(e, "image", None) and (getattr(e.image, "proxy_url", None) or getattr(e.image, "url", None)) and str(getattr(e.image, "proxy_url", None) or getattr(e.image, "url", None)) in attachment_urls:
                continue
            self.embeds += await Embed(e, self.guild, self.pytz_timezone, self.military_time).flow()
            
        async def flush_media_group(group):
            if not group: return ""
            html_output, start = "", 0
            splits = [n] if (n := len(group)) <= 4 else [9] if n == 9 else [n-3, 3] if n in (5, 6) else [n-6, 3, 3] if n in (7, 8) else [1, 9] if n == 10 else [9]
            for s in splits:
                html_output += await AttachmentGrid(group[start:start+s], self.guild, len(group)).flow()
                start += s
            return html_output
            
        for a in processed_attachments:
            if getattr(a, "content_type", None) and ("image" in a.content_type or "video" in a.content_type):
                media_group.append(a)
            else:
                if media_group:
                    self.attachments += await flush_media_group(media_group)
                    media_group = []
                self.attachments += await Attachment(a, self.guild).flow()
                
        if media_group:
            self.attachments += await flush_media_group(media_group)

        for r in getattr(self.message, "reactions", []):
            self.reactions += await Reaction(r, self.guild).flow()
            
        if self.reactions:
            self.reactions = f'<div class="chatlog__reactions">{self.reactions}</div>'

    async def wrap_forwarded(self):
        if self.forwarded:
            self.rendered_content = f'<div class="quote"><div>{message_forwarded}{self.rendered_content}{self.attachments}{self.forwarded_embeds}{self.components}</div></div>'
            self.attachments, self.forwarded_embeds, self.components = "", "", ""

    async def build_message_template(self):
        if not await self.generate_message_divider():
            self.message_html += await fill_out(self.guild, message_body, [
                ("MESSAGE_ID", str(self.message.id)), ("MESSAGE_CONTENT", self.rendered_content, PARSE_MODE_NONE),
                ("EMBEDS", self.embeds, PARSE_MODE_NONE), ("ATTACHMENTS", self.attachments, PARSE_MODE_NONE),
                ("COMPONENTS", self.components, PARSE_MODE_NONE), ("EMOJI", self.reactions, PARSE_MODE_NONE),
                ("TIMESTAMP", self.message_created_at, PARSE_MODE_NONE),
                ("TIME", self.message_created_at.split(maxsplit=4)[4], PARSE_MODE_NONE)
            ])

    async def generate_message_divider(self, channel_audit=False):
        if channel_audit or self.previous_message is None or self.message.reference != "" or self.previous_message.type is not discord.MessageType.default or self.interaction != "" or self.previous_message.author.id != self.message.author.id or getattr(self.message, "webhook_id", None) is not None or self.message.created_at > (self.previous_message.created_at + datetime.timedelta(minutes=4)):
            if self.previous_message is not None:
                self.message_html += await fill_out(self.guild, end_message, [])
            if channel_audit:
                self.audit = True
                return True
                
            time_val = self.message.created_at if getattr(self.message.created_at, "tzinfo", None) else timezone("UTC").localize(self.message.created_at)
            formatted_time = time_val.astimezone(timezone(self.pytz_timezone)).strftime("%d-%m-%Y %H:%M") if self.military_time else time_val.astimezone(timezone(self.pytz_timezone)).strftime("%d-%m-%Y %I:%M %p")
            
            self.message_html += await fill_out(self.guild, start_message, [
                ("REFERENCE_SYMBOL", "<div class='chatlog__followup-symbol'></div>" if self.message.reference != "" or self.interaction else "", PARSE_MODE_NONE),
                ("REFERENCE", self.message.reference if self.message.reference else self.interaction, PARSE_MODE_NONE),
                ("AVATAR_URL", str(self.message.author.display_avatar if hasattr(self.message.author, "display_avatar") and self.message.author.display_avatar else DiscordUtils.default_avatar), PARSE_MODE_NONE),
                ("NAME_TAG", await discriminator(self.message.author.name, getattr(self.message.author, "discriminator", "0")), PARSE_MODE_NONE),
                ("USER_ID", str(self.message.author.id)),
                ("USER_COLOUR", await self._gather_user_colour(self.message.author)),
                ("USER_ICON", await self._gather_user_icon(self.message.author), PARSE_MODE_NONE),
                ("NAME", str(html.escape(self.message.author.display_name))),
                ("BOT_TAG", str(bot_tag_verified if self.message.author.bot and getattr(self.message.author.public_flags, "verified_bot", False) else bot_tag if self.message.author.bot else ""), PARSE_MODE_NONE),
                ("TIMESTAMP", str(self.message_created_at)),
                ("DEFAULT_TIMESTAMP", str(formatted_time), PARSE_MODE_NONE),
                ("MESSAGE_ID", str(self.message.id)),
                ("MESSAGE_CONTENT", self.rendered_content, PARSE_MODE_NONE),
                ("EMBEDS", self.embeds, PARSE_MODE_NONE),
                ("ATTACHMENTS", self.attachments, PARSE_MODE_NONE),
                ("COMPONENTS", self.components, PARSE_MODE_NONE),
                ("EMOJI", self.reactions, PARSE_MODE_NONE)
            ])
            return True

    @cache()
    async def _gather_member(self, author: discord.Member):
        if self.guild and (member := self.guild.get_member(author.id)):
            return member
        try:
            return await self.guild.fetch_member(author.id) if self.guild else None
        except Exception:
            return None

    async def _gather_user_colour(self, author: discord.Member):
        member = await self._gather_member(author)
        if member and str(member.colour) != '#000000':
            return f"color: {member.colour};"
        return "color: #FFFFFF;"

    async def _gather_user_icon(self, author: discord.Member):
        member = await self._gather_member(author)
        if member and hasattr(member, "display_icon") and member.display_icon:
            return f"<img class='chatlog__role-icon' src='{member.display_icon}' alt='Role Icon'>"
        elif member and hasattr(member, "top_role") and member.top_role and member.top_role.icon:
            return f"<img class='chatlog__role-icon' src='{member.top_role.icon}' alt='Role Icon'>"
        return ""

    def set_time(self, message: Optional[discord.Message] = None):
        msg = message if message else self.message
        return self.to_local_time_str(msg.created_at), self.to_local_time_str(msg.edited_at) if getattr(msg, "edited_at", None) else ""

    def to_local_time_str(self, time_val):
        t_val = time_val if getattr(self.message.created_at, "tzinfo", None) else timezone("UTC").localize(time_val)
        return t_val.astimezone(timezone(self.pytz_timezone)).strftime(self.time_format)

async def gather_messages(messages: List[discord.Message], guild: discord.Guild, pytz_timezone, military_time, attachment_handler) -> (str, dict):
    """Processes a list of Discord messages into combined transcript HTML strings."""
    message_html_chunks, meta_data, previous_message, message_dict = [], {}, None, {m.id: m for m in messages}
    
    if messages and "thread" in str(messages[0].channel.type) and getattr(messages[0], "reference", None):
        channel = guild.get_channel(messages[0].reference.channel_id) or await guild.fetch_channel(messages[0].reference.channel_id)
        messages[0] = await channel.fetch_message(messages[0].reference.message_id)
        messages[0].reference = None
        
    for message in messages:
        content_html, meta_data = await MessageConstruct(message, previous_message, pytz_timezone, military_time, guild, meta_data, message_dict, attachment_handler).construct_message()
        message_html_chunks.append(content_html)
        previous_message = message
        
    message_html_chunks.append("</div>")
    return "".join(message_html_chunks), meta_data

class TranscriptDAO:
    html: str
    def __init__(self, channel, limit, messages, pytz_timezone, military_time, fancy_times, before, after, support_dev, bot, attachment_handler, raise_exceptions=False):
        self.channel = channel
        self.messages = messages
        self.limit = int(limit) if limit else None
        self.military_time = military_time
        self.fancy_times = fancy_times
        self.before = before
        self.after = after
        self.support_dev = support_dev
        self.pytz_timezone = pytz_timezone
        self.attachment_handler = attachment_handler
        self.raise_exceptions = raise_exceptions
        
        setattr(discord.Guild, "timezone", self.pytz_timezone)
        if bot:
            pass_bot(bot)

    async def build_transcript(self):
        message_html, meta_data = await gather_messages(self.messages, self.channel.guild, self.pytz_timezone, self.military_time, self.attachment_handler)
        await self.export_transcript(message_html, meta_data)
        clear_cache()
        return self

    async def export_transcript(self, message_html: str, meta_data: str):
        guild_icon = self.channel.guild.icon if getattr(self.channel.guild, "icon", None) and len(self.channel.guild.icon) > 2 else DiscordUtils.default_avatar
        guild_name = html.escape(self.channel.guild.name)
        tz = pytz.timezone(self.pytz_timezone)
        
        meta_data_html_chunks = []
        for data in meta_data:
            user_data = meta_data[int(data)]
            username = str(user_data[0])[:-5] if re.match(r"^#\d{4}", str(user_data[0][-5:])) else str(user_data[0])
            discrim = str(user_data[0][-5:]) if re.match(r"^#\d{4}", str(user_data[0][-5:])) else ""
            
            meta_data_html_chunks.append(await fill_out(self.channel.guild, meta_data_temp, [
                ("USER_ID", str(data), PARSE_MODE_NONE),
                ("USERNAME", username, PARSE_MODE_NONE),
                ("DISCRIMINATOR", discrim, PARSE_MODE_NONE),
                ("BOT", str(user_data[2]), PARSE_MODE_NONE),
                ("CREATED_AT", str(user_data[1].astimezone(tz).strftime("%b %d, %Y")), PARSE_MODE_NONE),
                ("JOINED_AT", str(user_data[5].astimezone(tz).strftime("%b %d, %Y") if user_data[5] else "Unknown"), PARSE_MODE_NONE),
                ("GUILD_ICON", str(guild_icon), PARSE_MODE_NONE),
                ("DISCORD_ICON", str(DiscordUtils.logo), PARSE_MODE_NONE),
                ("MEMBER_ID", str(data), PARSE_MODE_NONE),
                ("USER_AVATAR", str(user_data[3]), PARSE_MODE_NONE),
                ("DISPLAY", str(user_data[6]), PARSE_MODE_NONE),
                ("MESSAGE_COUNT", str(user_data[4]), PARSE_MODE_NONE)
            ]))
            
        time_now = datetime.datetime.now(tz).strftime("%e %B %Y at %H:%M:%S (%Z)" if self.military_time else "%e %B %Y at %I:%M:%S %p (%Z)")
        channel_created_time = self.channel.created_at.astimezone(tz).strftime("%b %d, %Y (%H:%M:%S)" if self.military_time else "%b %d, %Y (%I:%M:%S %p)")
        
        self.html = await fill_out(self.channel.guild, total, [
            ("SERVER_NAME", f"{guild_name}"),
            ("GUILD_ID", str(self.channel.guild.id), PARSE_MODE_NONE),
            ("SERVER_AVATAR_URL", str(guild_icon), PARSE_MODE_NONE),
            ("CHANNEL_NAME", f"{self.channel.name}"),
            ("MESSAGE_COUNT", str(len(self.messages)), PARSE_MODE_NONE),
            ("MESSAGES", message_html, PARSE_MODE_NONE),
            ("META_DATA", "".join(meta_data_html_chunks), PARSE_MODE_NONE),
            ("DATE_TIME", str(time_now)),
            ("SUBJECT", await fill_out(self.channel.guild, channel_subject, [("LIMIT", f"latest {self.limit} messages" if self.limit else "start", PARSE_MODE_NONE), ("CHANNEL_NAME", self.channel.name), ("RAW_CHANNEL_TOPIC", str(self.channel.topic if isinstance(self.channel, discord.TextChannel) and self.channel.topic else ""))]), PARSE_MODE_NONE),
            ("CHANNEL_CREATED_AT", str(channel_created_time), PARSE_MODE_NONE),
            ("CHANNEL_TOPIC", str(await fill_out(self.channel.guild, channel_topic, [("CHANNEL_TOPIC", html.escape(self.channel.topic if isinstance(self.channel, discord.TextChannel) and self.channel.topic else ""))]) if self.channel.topic if isinstance(self.channel, discord.TextChannel) and self.channel.topic else "" else ""), PARSE_MODE_NONE),
            ("CHANNEL_ID", str(self.channel.id), PARSE_MODE_NONE),
            ("MESSAGE_PARTICIPANTS", str(len(meta_data)), PARSE_MODE_NONE),
            ("FANCY_TIME", await fill_out(self.channel.guild, fancy_time, [("TIME_FORMAT", "HH:mm" if self.military_time else "hh:mm A", PARSE_MODE_NONE), ("TIMEZONE", str(self.pytz_timezone), PARSE_MODE_NONE)]) if self.fancy_times else "", PARSE_MODE_NONE),
            ("SD", '<div class="meta__support"><a href="https://ko-fi.com/mahtoid">DONATE</a></div>' if self.support_dev else "", PARSE_MODE_NONE),
            ("SERVER_NAME_SAFE", f"{guild_name}", PARSE_MODE_HTML_SAFE),
            ("CHANNEL_NAME_SAFE", f"{html.escape(self.channel.name)}", PARSE_MODE_HTML_SAFE)
        ])

class Transcript(TranscriptDAO):
    """Execution wrapper to fetch the channel history and kick off building the transcript."""
    async def export(self):
        if not self.messages:
            self.messages = [message async for message in self.channel.history(limit=self.limit, before=self.before, after=self.after)]
        if not self.after:
            self.messages.reverse()
        try:
            return await super().build_transcript()
        except Exception:
            self.html = "Whoops! Something went wrong..."
            traceback.print_exc()
            print("Please send a screenshot of the above error to https://www.github.com/mahtoid/DiscordChatExporterPy")
            if self.raise_exceptions:
                raise
            return self

# =========================================================================
# 9. CLI SETUP (Mimicking DiscordChatExporter.Cli)
# =========================================================================
def setup_cli():
    """Sets up the argparse definitions mapping directly to DiscordChatExporter.Cli commands."""
    parser = argparse.ArgumentParser(prog="DiscordChatExporter.Cli", description="DiscordChatExporter.Cli v2.47.1")
    parser.add_argument("--version", action="version", version="DiscordChatExporter.Cli v2.47.1")
    
    subparsers = parser.add_subparsers(dest="command", help="COMMANDS")
    
    # ------------------------------------
    # `channels` command
    # ------------------------------------
    parser_channels = subparsers.add_parser("channels", help="Get the list of channels in a server.")
    parser_channels.add_argument("-g", "--guild", required=True, help="Server ID.")
    parser_channels.add_argument("-t", "--token", required=True, default=os.environ.get("DISCORD_TOKEN"), help="Authentication token. Environment variable: DISCORD_TOKEN.")
    parser_channels.add_argument("--include-vc", default="True", help='Include voice channels. Default: "True".')
    parser_channels.add_argument("--include-threads", choices=["None", "Active", "All"], default="None", help='Which types of threads should be included. Choices: "None", "Active", "All". Default: "None".')
    parser_channels.add_argument("-b", "--bot", default="False", help="This option doesn't do anything. Kept for backwards compatibility. Environment variable: DISCORD_TOKEN_BOT. Default: \"False\".")
    parser_channels.add_argument("--respect-rate-limits", default="True", help='Whether to respect advisory rate limits. If disabled, only hard rate limits (i.e. 429 responses) will be respected. Default: "True".')
    
    # ------------------------------------
    # `dm` command
    # ------------------------------------
    subparsers.add_parser("dm", help="Gets the list of all direct message channels.")
    
    # ------------------------------------
    # `export` command
    # ------------------------------------
    parser_export = subparsers.add_parser("export", help="Exports one or multiple channels.")
    parser_export.add_argument("-c", "--channel", nargs="+", required=True, help="Channel ID(s). If provided with category ID(s), all channels inside those categories will be exported.")
    parser_export.add_argument("-t", "--token", required=True, default=os.environ.get("DISCORD_TOKEN"), help="Authentication token. Environment variable: DISCORD_TOKEN.")
    parser_export.add_argument("-o", "--output", default="/home/user/.local/share/DiscordChatExporter", help="Output file or directory path. If a directory is specified, file names will be generated automatically based on the channel names and export parameters. Directory paths must end with a slash to avoid ambiguity. Supports template tokens, see the documentation for more info. Default: \"/home/user/.local/share/DiscordChatExporter\".")
    parser_export.add_argument("-f", "--format", choices=["PlainText", "HtmlDark", "HtmlLight", "Csv", "Json"], default="HtmlDark", help='Export format. Choices: "PlainText", "HtmlDark", "HtmlLight", "Csv", "Json". Default: "HtmlDark".')
    parser_export.add_argument("--after", help="Only include messages sent after this date or message ID.")
    parser_export.add_argument("--before", help="Only include messages sent before this date or message ID.")
    parser_export.add_argument("-p", "--partition", help="Split the output into partitions, each limited to the specified number of messages (e.g. '100') or file size (e.g. '10mb').")
    parser_export.add_argument("--include-threads", choices=["None", "Active", "All"], default="None", help='Which types of threads should be included. Choices: "None", "Active", "All". Default: "None".')
    parser_export.add_argument("--filter", help="Only include messages that satisfy this filter. See the documentation for more info.")
    parser_export.add_argument("--parallel", default="1", help='Limits how many channels can be exported in parallel. Default: "1".')
    parser_export.add_argument("--reverse", default="False", help='Export messages in reverse chronological order (newest first). Default: "False".')
    parser_export.add_argument("--markdown", default="True", help='Process markdown, mentions, and other special tokens. Default: "True".')
    parser_export.add_argument("--media", default="False", help='Download assets referenced by the export (user avatars, attached files, embedded images, etc.). Default: "False".')
    parser_export.add_argument("--reuse-media", default="False", help='Reuse previously downloaded assets to avoid redundant requests. Default: "False".')
    parser_export.add_argument("--media-dir", help="Download assets to this directory. If not specified, the asset directory path will be derived from the output path.")
    parser_export.add_argument("--dateformat", default="MM/dd/yyyy h:mm tt", help='This option doesn\'t do anything. Kept for backwards compatibility. Default: "MM/dd/yyyy h:mm tt".')
    parser_export.add_argument("--locale", help="Locale to use when formatting dates and numbers. If not specified, the default system locale will be used.")
    parser_export.add_argument("--utc", default="False", help='Normalize all timestamps to UTC+0. Default: "False".')
    parser_export.add_argument("--fuck-russia", default="False", help='Don\'t print the Support Ukraine message to the console. Environment variable: FUCK_RUSSIA. Default: "False".')
    parser_export.add_argument("-b", "--bot", default="False", help="This option doesn't do anything. Kept for backwards compatibility. Environment variable: DISCORD_TOKEN_BOT. Default: \"False\".")
    parser_export.add_argument("--respect-rate-limits", default="True", help='Whether to respect advisory rate limits. If disabled, only hard rate limits (i.e. 429 responses) will be respected. Default: "True".')
    
    # ------------------------------------
    # Additional placeholder commands
    # ------------------------------------
    for cmd, desc in zip(["exportall", "exportdm", "exportguild", "guide", "guilds"], [
        "Exports all accessible channels.",
        "Exports all direct message channels.",
        "Exports all channels within the specified server.",
        "Explains how to obtain the token, server or channel ID.",
        "Gets the list of accessible servers."
    ]): 
        subparsers.add_parser(cmd, help=desc)
        
    return parser.parse_args()

# =========================================================================
# 10. MAIN EXECUTION BLOCK
# =========================================================================
if __name__ == "__main__":
    # Parses command line arguments equivalent to the provided C# application structure.
    # To run a transcript generation via code natively, one would interface 
    # with the Transcript() class or quick_export() methods natively.
    args = setup_cli()
    print(f"Parsed CLI arguments for command: {args.command}")
    print(vars(args))