"""Точка входа Елены."""
import sys
from gui_app import run


def main():
    try:
        run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()