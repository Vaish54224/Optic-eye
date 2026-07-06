import sys
import tkinter as tk

def main():
    if len(sys.argv) < 3:
        return
    title = sys.argv[1]
    message = sys.argv[2]
    
    root = tk.Tk()
    root.title(title)
    
    # Hide window decoration (frameless)
    root.overrideredirect(True)
    
    # Keep on top of all windows (always-on-top)
    root.attributes("-topmost", True)
    
    # Screen dimensions and positioning (bottom right)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    
    width = 340
    height = 110
    x = screen_width - width - 20
    y = screen_height - height - 50 # slightly above the Windows taskbar
    
    root.geometry(f"{width}x{height}+{x}+{y}")
    
    # Border & Casing (alert-coral border)
    border_frame = tk.Frame(root, bg="#ff6b5e", bd=1)
    border_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
    
    content_frame = tk.Frame(border_frame, bg="#12151c")
    content_frame.place(relx=0.01, rely=0.01, relwidth=0.98, relheight=0.98)
    
    # Title Label (with warning emoji)
    title_label = tk.Label(
        content_frame, 
        text="⚠️ " + title, 
        fg="#ff6b5e", 
        bg="#12151c", 
        font=("Segoe UI", 10, "bold"),
        anchor="w"
    )
    title_label.pack(fill="x", padx=12, pady=(10, 4))
    
    # Message Label
    msg_label = tk.Label(
        content_frame, 
        text=message, 
        fg="#e8ecf1", 
        bg="#12151c", 
        font=("Segoe UI", 9),
        wraplength=310,
        justify="left",
        anchor="nw"
    )
    msg_label.pack(fill="both", expand=True, padx=12, pady=(0, 10))
    
    # Close after 8 seconds automatically
    root.after(8000, root.destroy)
    
    # Support close click on window click
    root.bind("<Button-1>", lambda e: root.destroy())
    
    root.mainloop()

if __name__ == "__main__":
    main()
