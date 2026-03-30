import re
import logging

from config import Config

logger = logging.getLogger(__name__)

# Devs advertising themselves — NOT hiring posts
SELF_PROMO = re.compile(
    r"("
    r"i\s*('m|am)\s*.{0,50}(developer|dev|engineer|freelancer|designer|coder|programmer)\b|"
    r"i\s*am\s*.{0,50}(developer|dev|engineer|freelancer|designer|coder|programmer)\b|"
    r"i\s*(can|will|would)\s*(build|create|develop|design|make|code|deliver|help\s*you)|"
    r"i\s*(specialize|offer|provide|focus\s*on|work\s*with)|"
    r"hire\s*me|my\s*(portfolio|services|work|github|website|rates?)|"
    r"check\s*(out\s*)?my|here\s*('s|is)\s*my|"
    r"i\s*('m|am)\s*(available|open\s*to|looking\s*for\s*(work|client|project|gig|opportunity))|"
    r"i\s*(need|want)\s*(a\s*)?(client|project|work|gig)|"
    r"(dm|contact|message|reach)\s*(me|out)\s*(for|if\s*you\s*need)|"
    r"i\s*have\s*\d+\s*(year|yr)|"
    r"my\s*(expertise|experience|stack|skills|tech\s*stack)"
    r")",
    re.IGNORECASE,
)


class PreFilter:
    def __init__(self, config: Config):
        self._min_length = config.min_message_length
        self._keywords = [kw.lower() for kw in config.prefilter_keywords]
        self._pattern = re.compile(
            "|".join(re.escape(kw) for kw in self._keywords),
            re.IGNORECASE,
        )

    def should_classify(self, message: dict) -> bool:
        """Return True if the message is worth sending for classification."""
        if message.get("author", {}).get("bot", False):
            return False

        content = message.get("content", "")

        if len(content) < self._min_length:
            return False

        if not self._pattern.search(content):
            return False

        # Skip self-promotion (devs advertising themselves)
        if SELF_PROMO.search(content):
            return False

        logger.debug(
            f"Pre-filter passed: {message.get('author', {}).get('username')} "
            f"in #{message.get('_channel_name', 'unknown')}"
        )
        return True
