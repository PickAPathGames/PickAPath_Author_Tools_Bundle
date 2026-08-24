# author_tools/pickquick.py

from engine.tools.pickquick import main
import sys

if __name__ == "__main__":
    result = main()

    if result["status"] == "error":
        sys.exit(1)

