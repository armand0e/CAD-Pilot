"""Small deterministic image measurements; none imply CAD correctness."""
from PIL import ImageChops, ImageStat


def has_application_content(image) -> bool:
    # A bright cursor on an otherwise black framebuffer is not a ready application.
    histogram = image.convert("L").histogram()
    return sum(histogram[16:]) / (image.width * image.height) >= .02


def screen_change(before, after) -> float:
    if before.size != after.size:
        return 1.0
    a = before.convert("L").resize((160, 100))
    b = after.convert("L").resize((160, 100))
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] / 255.0


class RecoveryBudget:
    """Separate inconclusive observation from concrete failed approaches.

    Observational ambiguity allows a short continuation; persistent ambiguity and repeated
    bad outcomes still have explicit bounds. Success/progress starts a new recovery episode.
    """
    def __init__(self, failures=4, uncertain=6):
        self.failure_limit, self.uncertain_limit = failures, uncertain
        self.failures = self.uncertain = 0

    def observe(self, status: str) -> bool:
        if status in ("achieved", "progress"):
            self.failures = self.uncertain = 0
        elif status == "blocked":
            self.failures += 1
        else:
            self.uncertain += 1
        return self.failures >= self.failure_limit or self.uncertain >= self.uncertain_limit
