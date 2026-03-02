print ("Hello, I control the computer!")

import tkinter as tk

root = tk.Tk()
root.title("My Program")

label = tk.Label(root, text="Hello, I control the computer!", font=("Arial", 16))
label.pack(padx=20, pady=20)

root.mainloop()