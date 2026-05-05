import warnings

warnings.filterwarnings(
    "ignore",
    category=FutureWarning
)

from .ai import main  # noqa: E402

if __name__ == "__main__":
    main()
