import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # necesario para el OCR en paralelo dentro del .exe
    from prisma.app import main
    sys.exit(main())
