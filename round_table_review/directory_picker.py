"""Show the dataset folder dialog on a GUI process's main thread."""
import json
import sys
import tkinter as tk
from tkinter.filedialog import askdirectory


def main():
    root = tk.Tk()
    root.withdraw()
    # Keep the native modal dialog above the browser, even with a hidden owner.
    root.attributes('-topmost', True)
    root.update()
    try:
        options = dict(parent=root, title='选择图片与 JSON 所在的数据集目录', mustexist=True)
        if len(sys.argv) > 1:
            options['initialdir'] = sys.argv[1]
        directory = askdirectory(**options)
    finally:
        root.destroy()
    # ASCII JSON avoids Windows console encodings corrupting Chinese paths.
    print(json.dumps(directory, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
