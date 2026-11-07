"""LabPilot screen for the Raspberry Pi (Tkinter).   Run:  python3 gui.py

Tabs: Run | Protocol | Hardware | Camera | Log | Settings | Faults
Works in Simulation mode with no hardware; the same screen drives the real Arduino and camera.
"""
import base64, csv, os, queue, subprocess, threading, time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
from protocol import Step, load_protocol, validate
from link import SerialLink, MockLink
from vision import Vision, MockVision, NullVision
from logger import RunLogger, FIELDS
from main import run_sample, settle

HERE = os.path.dirname(os.path.abspath(__file__))
COLORS = {"PENDING": "#eeeeee", "RUNNING": "#fff3b0", "ACCEPTED": "#c8f0c8",
          "REJECTED": "#ffd2a8", "ABORTED": "#ffb3b3"}
VIEW_W, VIEW_H = 320, 240            # small live view on the Run tab
CAM_W, CAM_H = 480, 360              # bigger live view on the Camera tab (the overlay text stays readable)

SETTING_FIELDS = [
    ("STEPS_PER_ML", "Pump: steps per mL", "float"),
    ("DENSITY_G_PER_ML", "Liquid density (g/mL)", "float"),
    ("SYRINGE_CAPACITY_ML", "Syringe capacity (mL)", "float"),
    ("MAX_STEPS_FACTOR", "Safety cap on steps (x expected)", "float"),
    ("CONTAINER_MIN_G", "Container detected above (g)", "float"),
    ("MIN_GAIN_ML", "Minimum flow to count (mL)", "float"),
    ("MISMATCH_TOL_ML", "Commanded vs weighed tolerance (mL)", "float"),
    ("CONTAINER_TARGET", "Container target spot  x,y (0-1)", "pair"),
    ("CONTAINER_TOL_PX", "Container: allowed offset (px)", "float"),
    ("CONTAINER_MM_PER_PX", "Container: mm per pixel (0 = px only)", "float"),
    ("CONTAINER_DARK_FRAC", "Container: 'dark' below x of backlight", "float"),
    ("CONTAINER_MIN_AREA_FRAC", "Container: min silhouette size (0-1)", "float"),
    ("QUALITY_MIN_BRIGHTNESS", "Image guard: too dark below", "float"),
    ("QUALITY_MAX_SATURATED", "Image guard: overexposed above (0-1)", "float"),
    ("QUALITY_MIN_SHARPNESS", "Image guard: blurry below", "float"),
    ("PLUNGER_MARKER", "Plunger marker (bright or dark)", "str"),
    ("PLUNGER_MIN_CONTRAST", "Plunger marker: min contrast", "float"),
    ("PLUNGER_MIN_PX", "Plunger: min travel to count (px)", "float"),
    ("PLUNGER_PX_PER_ML", "Plunger: pixels per mL (0 = off)", "float"),
    ("PLUNGER_SLIP_TOL", "Plunger: slip tolerance (fraction)", "float"),
    ("MOTION_DIFF_THRESH", "Fallback motion threshold (no marker)", "float"),
    ("SERIAL_BAUD", "Serial baud rate", "int"),
    ("CAMERA_TYPE", "Camera type (picamera or usb)", "str"),
    ("CAMERA_INDEX", "USB camera index", "int"),
    ("CONTAINER_ROI", "Container region  x,y,w,h (0-1)", "roi"),
    ("PLUNGER_ROI", "Plunger region  x,y,w,h (0-1)", "roi"),
]

FAULTS = [
    ("NO_CONTAINER", "The scale does not register a container.", "Place the container on the load cell."),
    ("MISPLACED", "Scale sees a container but the camera does not see it within the allowed offset of the target spot (the offset in px is logged).", "Re-position the container in the holder; check the target spot in the Camera tab."),
    ("MOTOR_STALL", "The plunger is blocked or hit the end-stop.", "Check the plunger, tube and end-stop."),
    ("NO_FLOW", "Plunger moves but no mass is gained.", "Pinched or blocked tube, empty syringe, air."),
    ("PLUNGER_SLIP", "The plunger marker moved clearly less (or more) than the commanded steps.", "Skipped steps: lower the speed/acceleration, check the coupling and motor current; or re-calibrate px per mL."),
    ("MISMATCH", "Delivered mass differs from the commanded volume.", "Check STEPS_PER_ML, bubbles, leaks."),
    ("OVERFILL", "More than target + tolerance was delivered.", "Check scale calibration and the stop threshold."),
    ("OUT_OF_TOLERANCE", "Delivered volume is outside the protocol tolerance.", "Recalibrate; reject and redo the sample."),
    ("LINK_ERROR", "The Arduino stopped answering.", "Check the USB cable and the port."),
    ("(camera UNRELIABLE)", "Not a fault: the image was too dark, overexposed or blurry (or the marker was lost), so the camera did not vote. The run continues on the scale alone and the log says UNRELIABLE.", "Check the backlight, focus and exposure; see the live overlay in the Camera tab."),
]


class UserStop(Exception):
    pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("LabPilot - self-verifying syringe pump")
        root.geometry("1040x700")
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

        self.q = queue.Queue()
        self.hw_lock = threading.Lock()
        self.cont, self.stop_flag, self.pause_flag = threading.Event(), threading.Event(), threading.Event()
        self.thread = None
        self.link = None
        self.vision = None
        self.png = None
        self.prog_frac = 0.0
        self.suggest = None
        self.suggest_px = None
        self.steps, self.draft = [], []
        self.counts = {"ACCEPTED": 0, "REJECTED": 0, "ABORTED": 0}

        self.sim = tk.BooleanVar(value=True)
        self.use_cam = tk.BooleanVar(value=True)
        self.port = tk.StringVar(value="/dev/ttyACM0")
        self.faults = tk.StringVar(value="none,no_container,no_flow")

        self.build()
        default = os.path.join(HERE, "protocols", "P-001.csv")
        if os.path.exists(default):
            self.load_file(default, quiet=True)
        self.refresh_conn()

        threading.Thread(target=self.mass_loop, daemon=True).start()
        threading.Thread(target=self.preview_loop, daemon=True).start()
        root.after(100, self.poll)
        root.after(500, self.redraw)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ================= layout =================
    def build(self):
        st = ttk.Style()
        st.configure("Treeview", font=("TkDefaultFont", 11), rowheight=30)
        st.configure("Treeview.Heading", font=("TkDefaultFont", 11, "bold"))
        st.configure("Big.TButton", font=("TkDefaultFont", 13, "bold"), padding=8)
        st.configure("TNotebook.Tab", font=("TkDefaultFont", 12, "bold"), padding=(14, 6))

        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Simulation", variable=self.sim,
                        command=self.on_sim_toggle).pack(side="left", padx=4)
        self.l_ard = ttk.Label(bar, text="", font=("TkDefaultFont", 11, "bold"))
        self.l_ard.pack(side="left", padx=(16, 2))
        self.b_ard = ttk.Button(bar, text="Connect", command=self.toggle_arduino)
        self.b_ard.pack(side="left")
        self.l_cam = ttk.Label(bar, text="", font=("TkDefaultFont", 11, "bold"))
        self.l_cam.pack(side="left", padx=(16, 2))
        self.b_cam = ttk.Button(bar, text="Connect", command=self.toggle_camera)
        self.b_cam.pack(side="left")
        ttk.Checkbutton(bar, text="Camera checks", variable=self.use_cam).pack(side="left", padx=16)

        self.status = ttk.Label(self.root, text="Ready.", relief="sunken", anchor="w", padding=4)
        self.status.pack(side="bottom", fill="x")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=6, pady=4)
        tabs = {}
        for name in ("Run", "Protocol", "Hardware", "Camera", "Log", "Settings", "Faults"):
            tabs[name] = ttk.Frame(self.nb, padding=6)
            self.nb.add(tabs[name], text=name)
        self.build_run(tabs["Run"])
        self.build_protocol(tabs["Protocol"])
        self.build_hardware(tabs["Hardware"])
        self.build_camera(tabs["Camera"])
        self.build_log(tabs["Log"])
        self.build_settings(tabs["Settings"])
        self.build_faults(tabs["Faults"])

    def build_run(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(f, padding=(10, 0))
        right.grid(row=0, column=1, sticky="n")

        self.proto_lbl = ttk.Label(left, text="", font=("TkDefaultFont", 11, "italic"))
        self.proto_lbl.pack(anchor="w")
        cols = ("sample", "target", "tol", "weighed", "camera", "status")
        heads = ("Sample", "Target (mL)", "Tol. (mL)", "Weighed (mL)", "Camera", "Status")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=6)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=118, anchor="center")
        self.tree.column("sample", width=120)
        self.tree.column("status", width=140)
        for tag, col in COLORS.items():
            self.tree.tag_configure(tag, background=col)
        self.tree.pack(fill="both", expand=True, pady=4)

        self.instr = ttk.Label(left, text="", font=("TkDefaultFont", 14, "bold"),
                               wraplength=620, anchor="center", justify="center")
        self.instr.pack(fill="x", pady=6)

        wit = ttk.LabelFrame(left, text="The three witnesses", padding=6)
        wit.pack(fill="x", pady=4)
        self.w_cmd, self.w_wgh = tk.StringVar(value="-"), tk.StringVar(value="-")
        self.w_cam, self.w_dec = tk.StringVar(value="-"), tk.StringVar(value="-")
        for r, (lab, var) in enumerate((("Motor command (steps)", self.w_cmd), ("Scale (weighed)", self.w_wgh),
                                        ("Camera", self.w_cam), ("Decision", self.w_dec))):
            ttk.Label(wit, text=lab + ":").grid(row=r // 2, column=(r % 2) * 2, sticky="w", padx=4)
            ttk.Label(wit, textvariable=var, font=("TkDefaultFont", 11, "bold")).grid(row=r // 2, column=(r % 2) * 2 + 1, sticky="w", padx=8)

        bar = ttk.Frame(left)
        bar.pack(pady=6)
        self.b_start = ttk.Button(bar, text="START", style="Big.TButton", command=self.start)
        self.b_pause = ttk.Button(bar, text="PAUSE", style="Big.TButton", command=self.toggle_pause, state="disabled")
        self.b_cont = ttk.Button(bar, text="CONTINUE", style="Big.TButton", command=self.cont.set, state="disabled")
        self.b_stop = ttk.Button(bar, text="STOP", style="Big.TButton", command=self.stop, state="disabled")
        for b in (self.b_start, self.b_pause, self.b_cont, self.b_stop):
            b.pack(side="left", padx=6)

        sf = ttk.Frame(left)
        sf.pack(fill="x")
        ttk.Label(sf, text="Simulated faults (one per sample):").pack(side="left")
        ttk.Entry(sf, textvariable=self.faults, width=40).pack(side="left", padx=6)
        self.summary = ttk.Label(left, text="", font=("TkDefaultFont", 12))
        self.summary.pack(pady=4)

        self.cv_run = tk.Canvas(right, width=VIEW_W, height=VIEW_H, bg="#222", highlightthickness=0)
        self.cv_run.pack()
        self.mass_lbl = ttk.Label(right, text="Scale: - g", font=("TkDefaultFont", 16, "bold"))
        self.mass_lbl.pack(pady=(10, 2))
        self.pbar = ttk.Progressbar(right, length=VIEW_W, maximum=100)
        self.pbar.pack()
        ttk.Label(right, text="dose progress", foreground="gray").pack()

    def build_protocol(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.e_pid, self.e_sid = tk.StringVar(value="P-001"), tk.StringVar()
        self.e_tgt, self.e_tol = tk.StringVar(), tk.StringVar(value="0.2")
        for c, (lab, var, w) in enumerate((("Protocol ID", self.e_pid, 10), ("Sample ID", self.e_sid, 10),
                                           ("Target (mL)", self.e_tgt, 10), ("Tolerance (mL)", self.e_tol, 10))):
            ttk.Label(top, text=lab).grid(row=0, column=c, padx=4, sticky="w")
            ttk.Entry(top, textvariable=var, width=w).grid(row=1, column=c, padx=4)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        for text, cmd in (("Add", self.p_add), ("Update selected", self.p_update),
                          ("Delete selected", self.p_delete), ("Clear", self.p_clear)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)
        cols = ("pid", "sid", "tgt", "tol")
        self.ptree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c, h in zip(cols, ("Protocol", "Sample", "Target (mL)", "Tolerance (mL)")):
            self.ptree.heading(c, text=h)
            self.ptree.column(c, width=150, anchor="center")
        self.ptree.pack(fill="both", expand=True)
        self.ptree.bind("<<TreeviewSelect>>", self.p_select)
        bar2 = ttk.Frame(f)
        bar2.pack(fill="x", pady=6)
        ttk.Button(bar2, text="Load file (CSV / JSON)...", command=self.p_load).pack(side="left", padx=3)
        ttk.Button(bar2, text="Save as CSV...", command=self.p_save).pack(side="left", padx=3)
        ttk.Button(bar2, text="USE THIS PROTOCOL", style="Big.TButton", command=self.p_use).pack(side="right", padx=3)

    def build_hardware(self, f):
        a = ttk.LabelFrame(f, text="Arduino connection", padding=8)
        a.pack(fill="x", pady=4)
        ttk.Label(a, text="Port:").pack(side="left")
        self.port_box = ttk.Combobox(a, textvariable=self.port, width=18, values=self.list_ports())
        self.port_box.pack(side="left", padx=6)
        ttk.Button(a, text="Refresh ports", command=lambda: self.port_box.config(values=self.list_ports())).pack(side="left")
        ttk.Label(a, text="(used when Simulation is off)", foreground="gray").pack(side="left", padx=10)

        s = ttk.LabelFrame(f, text="Scale (load cell)", padding=8)
        s.pack(fill="x", pady=4)
        self.hw_mass = ttk.Label(s, text="- g", font=("TkDefaultFont", 20, "bold"), width=12)
        self.hw_mass.pack(side="left")
        ttk.Button(s, text="Tare", command=self.do_tare).pack(side="left", padx=6)
        ttk.Label(s, text="Known weight (g):").pack(side="left", padx=(20, 2))
        self.cal_g = tk.StringVar(value="100")
        ttk.Entry(s, textvariable=self.cal_g, width=8).pack(side="left")
        ttk.Button(s, text="Calibrate scale", command=self.do_scale_cal).pack(side="left", padx=6)

        p = ttk.LabelFrame(f, text="Pump", padding=8)
        p.pack(fill="x", pady=4)
        self.dose_ml, self.ref_g = tk.StringVar(value="5"), tk.StringVar()
        ttk.Label(p, text="Volume (mL):").grid(row=0, column=0, sticky="w")
        ttk.Entry(p, textvariable=self.dose_ml, width=8).grid(row=0, column=1, padx=6)
        ttk.Button(p, text="Test dose", command=lambda: self.do_dose(False)).grid(row=0, column=2, padx=6)
        ttk.Button(p, text="EMERGENCY STOP", command=self.stop).grid(row=0, column=3, padx=20)
        ttk.Label(p, text="Reference balance (g, optional):").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(p, textvariable=self.ref_g, width=8).grid(row=1, column=1, padx=6)
        ttk.Button(p, text="Calibrate STEPS_PER_ML", command=lambda: self.do_dose(True)).grid(row=1, column=2, padx=6)
        self.b_apply = ttk.Button(p, text="Apply suggested value", command=self.apply_suggest, state="disabled")
        self.b_apply.grid(row=1, column=3, padx=20)
        self.b_apply_px = ttk.Button(p, text="Apply plunger px/mL", command=self.apply_suggest_px, state="disabled")
        self.b_apply_px.grid(row=1, column=4, padx=6)
        self.hw_msg = ttk.Label(f, text="", wraplength=900, font=("TkDefaultFont", 11))
        self.hw_msg.pack(fill="x", pady=8)

    def build_camera(self, f):
        self.cv_cam = tk.Canvas(f, width=CAM_W, height=CAM_H, bg="#222", highlightthickness=0)
        self.cv_cam.pack(side="left", anchor="n")
        r = ttk.Frame(f, padding=(14, 0))
        r.pack(side="left", anchor="n")
        ttk.Label(r, text="Blue / orange boxes = container / plunger search areas (change in Settings).\n"
                          "Yellow cross + circle = target spot and its tolerance.\n"
                          "Green box = container in position, red = misplaced (offset in px).\n"
                          "Orange ring = plunger marker. The top line shows the image check.",
                  foreground="gray", justify="left").pack(anchor="w", pady=4)
        ttk.Button(r, text="1. Check the empty holder view", command=self.cam_ref).pack(anchor="w", pady=4)
        ttk.Button(r, text="2. Place container correctly, then: Set target here", command=self.cam_target).pack(anchor="w", pady=4)
        ttk.Button(r, text="3. Test container check", command=self.cam_test).pack(anchor="w", pady=4)
        ttk.Button(r, text="Save annotated snapshot", command=self.cam_snap).pack(anchor="w", pady=4)
        self.cam_msg = ttk.Label(r, text="", wraplength=360, font=("TkDefaultFont", 11))
        self.cam_msg.pack(anchor="w", pady=10)

    def build_log(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Open logs folder", command=self.open_logs).pack(side="left", padx=3)
        ttk.Button(bar, text="Clear view", command=lambda: self.ltree.delete(*self.ltree.get_children())).pack(side="left", padx=3)
        ttk.Label(bar, text="Every sample is also saved to logs/ (CSV + JSON lines + a photo when the camera is on).",
                  foreground="gray").pack(side="left", padx=10)
        self.ltree = ttk.Treeview(f, columns=FIELDS, show="headings", height=14)
        for c in FIELDS:
            self.ltree.heading(c, text=c)
            self.ltree.column(c, width=100, anchor="center")
        self.ltree.column("t", width=150)
        self.ltree.pack(fill="both", expand=True)

    def build_settings(self, f):
        self.set_vars = {}
        half = (len(SETTING_FIELDS) + 1) // 2                      # two columns so everything fits on the screen
        for i, (key, label, kind) in enumerate(SETTING_FIELDS):
            r, c = i % half, (i // half) * 2
            ttk.Label(f, text=label).grid(row=r, column=c, sticky="w", padx=(14 if c else 4, 4), pady=2)
            v = tk.StringVar()
            self.set_vars[key] = v
            ttk.Entry(f, textvariable=v, width=18).grid(row=r, column=c + 1, padx=6)
        self.fill_settings()
        bar = ttk.Frame(f)
        bar.grid(row=half, column=0, columnspan=4, pady=10, sticky="w")
        ttk.Button(bar, text="Save settings", style="Big.TButton", command=self.save_settings).pack(side="left", padx=4)
        ttk.Button(bar, text="Reset to defaults", command=self.reset_settings).pack(side="left", padx=4)

    def build_faults(self, f):
        t = ttk.Treeview(f, columns=("code", "meaning", "do"), show="headings", height=10)
        for c, h, w in (("code", "Fault", 160), ("meaning", "What it means", 420), ("do", "What to check", 380)):
            t.heading(c, text=h)
            t.column(c, width=w, anchor="w")
        for row in FAULTS:
            t.insert("", "end", values=row)
        t.pack(fill="both", expand=True)

    # ================= helpers =================
    def say(self, text):
        self.status.config(text=text)

    @staticmethod
    def list_ports():
        import glob
        return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")) or ["/dev/ttyACM0"]

    def refresh_conn(self):
        a, c = self.link, self.vision
        if a:
            self.l_ard.config(text="● Arduino: " + ("simulated" if isinstance(a, MockLink) else "connected"), foreground="#1a8f3c")
        else:
            self.l_ard.config(text="● Arduino: off", foreground="gray")
        if c:
            self.l_cam.config(text="● Camera: " + ("simulated" if isinstance(c, MockVision) else "connected"), foreground="#1a8f3c")
        else:
            self.l_cam.config(text="● Camera: off", foreground="gray")
        self.b_ard.config(text="Disconnect" if a else "Connect")
        self.b_cam.config(text="Disconnect" if c else "Connect")

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    # ================= connections =================
    def on_sim_toggle(self):
        if self.running():
            return
        self.disconnect_all()

    def disconnect_all(self):
        for x in (self.vision, self.link):
            try:
                x and x.close()
            except Exception:
                pass
        self.vision = self.link = None
        self.refresh_conn()

    def toggle_arduino(self):
        if self.running():
            return self.say("Stop the run first.")
        if self.link:
            try:
                self.link.close()
            except Exception:
                pass
            if isinstance(self.vision, MockVision):
                self.vision = None
            self.link = None
            return self.refresh_conn()
        sim, port = self.sim.get(), self.port.get().strip()
        self.say("Connecting to the Arduino...")
        threading.Thread(target=self.connect_arduino, args=(sim, port), daemon=True).start()

    def toggle_camera(self):
        if self.running():
            return self.say("Stop the run first.")
        if self.vision:
            try:
                self.vision.close()
            except Exception:
                pass
            self.vision = None
            return self.refresh_conn()
        self.say("Connecting the camera...")
        threading.Thread(target=self.connect_camera, args=(self.sim.get(),), daemon=True).start()

    def connect_arduino(self, sim, port):
        try:
            self.link = MockLink(delay=0.02) if sim else SerialLink(port)
            self.q.put(("status", "Arduino connected." if not sim else "Simulated Arduino ready."))
        except Exception as e:
            self.q.put(("status", f"Arduino connection failed: {e}"))
        self.q.put(("conn",))

    def connect_camera(self, sim):
        try:
            if sim:
                if self.link is None:
                    self.link = MockLink(delay=0.02)
                self.vision = MockVision(self.link)
            else:
                self.vision = Vision()
            self.q.put(("status", "Camera ready."))
        except Exception as e:
            self.q.put(("status", f"Camera connection failed: {e}"))
        self.q.put(("conn",))

    # ================= protocol tab =================
    def p_refresh(self):
        self.ptree.delete(*self.ptree.get_children())
        for i, s in enumerate(self.draft):
            self.ptree.insert("", "end", iid=str(i), values=(s.protocol_id, s.sample_id, s.target_ml, s.tolerance_ml))

    def p_read(self):
        try:
            return Step(self.e_pid.get().strip(), self.e_sid.get().strip(), float(self.e_tgt.get()), float(self.e_tol.get()))
        except ValueError:
            messagebox.showwarning("Protocol", "Target and tolerance must be numbers.")

    def p_add(self):
        s = self.p_read()
        if s:
            self.draft.append(s)
            self.p_refresh()
            self.e_sid.set("")

    def p_selected(self):
        sel = self.ptree.selection()
        return int(sel[0]) if sel else None

    def p_update(self):
        i, s = self.p_selected(), self.p_read()
        if i is not None and s:
            self.draft[i] = s
            self.p_refresh()

    def p_delete(self):
        i = self.p_selected()
        if i is not None:
            del self.draft[i]
            self.p_refresh()

    def p_clear(self):
        self.draft = []
        self.p_refresh()

    def p_select(self, _):
        i = self.p_selected()
        if i is not None and i < len(self.draft):
            s = self.draft[i]
            self.e_pid.set(s.protocol_id); self.e_sid.set(s.sample_id)
            self.e_tgt.set(str(s.target_ml)); self.e_tol.set(str(s.tolerance_ml))

    def p_load(self):
        p = filedialog.askopenfilename(initialdir=os.path.join(HERE, "protocols"),
                                       filetypes=[("Protocols", "*.csv *.json")])
        if p:
            self.load_file(p)

    def load_file(self, path, quiet=False):
        try:
            steps = load_protocol(path)
        except (ValueError, OSError) as e:
            if quiet:
                self.say(f"Protocol not loaded: {e}")
            else:
                messagebox.showerror("Protocol refused", str(e))
            return
        self.draft = list(steps)
        self.p_refresh()
        self.activate(steps, os.path.basename(path))

    def p_save(self):
        p = filedialog.asksaveasfilename(initialdir=os.path.join(HERE, "protocols"), defaultextension=".csv")
        if not p:
            return
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["protocol_id", "sample_id", "target_mL", "tolerance_mL"])
            for s in self.draft:
                w.writerow([s.protocol_id, s.sample_id, s.target_ml, s.tolerance_ml])
        self.say(f"Saved {p}")

    def p_use(self):
        try:
            validate(self.draft)
        except ValueError as e:
            return messagebox.showerror("Protocol refused", str(e))
        self.activate(list(self.draft), "edited on screen")
        self.nb.select(0)

    def activate(self, steps, name):
        if self.running():
            return self.say("Stop the run before changing the protocol.")
        self.steps = list(steps)
        self.proto_lbl.config(text=f"Active protocol: {name}  ({len(steps)} samples, {sum(s.target_ml for s in steps):g} mL total)")
        self.reset_table()
        self.instr.config(text="Protocol loaded. Press START.")

    def reset_table(self):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.steps):
            self.tree.insert("", "end", iid=str(i), tags=("PENDING",),
                             values=(f"{s.protocol_id} / {s.sample_id}", s.target_ml, s.tolerance_ml, "-", "-", "PENDING"))
        self.counts = {"ACCEPTED": 0, "REJECTED": 0, "ABORTED": 0}
        self.summary.config(text="")
        for v in (self.w_cmd, self.w_wgh, self.w_cam, self.w_dec):
            v.set("-")
        self.pbar["value"] = 0
        self.prog_frac = 0.0

    # ================= run =================
    def start(self):
        if self.running():
            return
        if not self.steps:
            return self.say("Load or build a protocol first (Protocol tab).")
        self.reset_table()
        opts = dict(sim=self.sim.get(), use_cam=self.use_cam.get(), port=self.port.get().strip(),
                    faults=[x.strip() for x in self.faults.get().split(",") if x.strip()], steps=list(self.steps))
        self.stop_flag.clear(); self.cont.clear(); self.pause_flag.clear()
        self.b_pause.config(text="PAUSE")
        self.b_start.config(state="disabled")
        self.b_pause.config(state="normal")
        self.b_stop.config(state="normal")
        self.thread = threading.Thread(target=self.work, args=(opts,), daemon=True)
        self.thread.start()

    def toggle_pause(self):
        if self.pause_flag.is_set():
            self.pause_flag.clear()
            self.b_pause.config(text="PAUSE")
        else:
            self.pause_flag.set()
            self.b_pause.config(text="RESUME")

    def stop(self):
        self.stop_flag.set()
        try:
            if self.link:
                self.link.stop()
        except Exception:
            pass
        self.say("STOP sent.")

    def ask(self, msg):
        self.q.put(("msg", msg))
        self.q.put(("need_continue", True))
        self.cont.clear()
        while not self.cont.wait(0.2):
            if self.stop_flag.is_set():
                raise UserStop()
        self.q.put(("need_continue", False))

    def gate(self):
        while True:
            if self.stop_flag.is_set():
                raise UserStop()
            if not self.pause_flag.is_set():
                return
            self.q.put(("msg", "Paused. Press RESUME to continue."))
            time.sleep(0.2)

    def work(self, opts):
        msg, summary, got_lock = "Run finished.", None, False
        try:
            if self.link is None:
                self.connect_arduino(opts["sim"], opts["port"])
            if self.link is None:
                raise RuntimeError("Arduino not connected")
            if opts["use_cam"] and self.vision is None:
                self.connect_camera(opts["sim"])
            if opts["use_cam"] and self.vision is None:
                raise RuntimeError("camera not available (untick 'Camera checks' to run on the scale alone)")
            link = self.link
            vision = self.vision if opts["use_cam"] else NullVision()
            if not self.hw_lock.acquire(blocking=False):
                raise RuntimeError("hardware is busy with another operation")
            got_lock = True
            if hasattr(link, "faults"):
                link.faults = list(opts["faults"])
            log = RunLogger(os.path.join(HERE, "logs"))
            for i, s in enumerate(opts["steps"]):
                self.gate()
                self.q.put(("row", i, "RUNNING", None))
                if hasattr(link, "simulate_placement"):
                    link.simulate_placement()
                    self.q.put(("msg", f"Sample {s.sample_id}: container placed (simulated)..."))
                    time.sleep(0.8)
                else:
                    self.ask(f"Place the container for sample {s.sample_id} ({s.target_ml} mL), then press CONTINUE.")
                self.q.put(("msg", f"Sample {s.sample_id}: checking, dosing, verifying..."))
                tg = s.target_ml * config.DENSITY_G_PER_ML
                rec = run_sample(s, link, vision, log,
                                 on_progress=lambda t, m, st, tg=tg: self.q.put(("prog", m, st, tg)))
                self.q.put(("row", i, rec["status"], rec))
                self.q.put(("log", rec))
                time.sleep(0.3)
            summary = log.summary()
        except UserStop:
            msg = "Run stopped by the operator."
        except Exception as e:
            msg = f"ERROR: {e}"
        finally:
            if got_lock:
                self.hw_lock.release()
            self.q.put(("done", msg, summary))

    # ================= hardware tab =================
    def hw_op(self, fn):
        if self.link is None:
            return self.say("Connect the Arduino first (top bar).")

        def run():
            if not self.hw_lock.acquire(blocking=False):
                return self.q.put(("status", "Hardware is busy."))
            try:
                out = fn()
                if out:
                    self.q.put(("hwmsg", out))
                    self.q.put(("status", out))
            except Exception as e:
                self.q.put(("hwmsg", f"Error: {e}"))
                self.q.put(("status", f"Error: {e}"))
            finally:
                self.hw_lock.release()
        threading.Thread(target=run, daemon=True).start()

    def do_tare(self):
        def fn():
            self.link.tare()
            return "Scale tared."
        self.hw_op(fn)

    def do_scale_cal(self):
        try:
            g = float(self.cal_g.get())
        except ValueError:
            return self.say("Enter the known weight in grams.")

        def fn():
            try:
                self.link.calibrate_scale(g)
            except Exception:
                return "The Arduino did not answer CAL. Scale calibration must be implemented in the Arduino code (optional CAL command)."
            return f"Scale calibrated with {g:g} g."
        self.hw_op(fn)

    def do_dose(self, calibrate):
        try:
            ml = float(self.dose_ml.get())
            if not 0 < ml <= config.SYRINGE_CAPACITY_ML:
                raise ValueError
            ref = float(self.ref_g.get()) if calibrate and self.ref_g.get().strip() else None
        except ValueError:
            return self.say(f"Enter a volume between 0 and {config.SYRINGE_CAPACITY_ML:g} mL (and a number for the balance).")

        def fn():
            link = self.link
            if hasattr(link, "simulate_placement"):
                link.faults = []
                link.simulate_placement()
            link.tare()
            cam = self.vision if self.vision is not None and not isinstance(self.vision, (MockVision, NullVision)) else None
            if cam:
                cam.start_watch()                      # track the plunger marker during the test dose

            def on_sample(t, m, s):
                if cam:
                    cam.tick()
                self.q.put(("prog", m, s, ml * config.DENSITY_G_PER_ML))
            res = link.dose(ml * config.DENSITY_G_PER_ML, int(ml * config.STEPS_PER_ML * config.MAX_STEPS_FACTOR),
                            on_sample=on_sample)
            cmd_ml = res.steps / config.STEPS_PER_ML
            pc = cam.plunger_result(cmd_ml) if cam else None
            mass = settle(link)
            vol = mass / config.DENSITY_G_PER_ML
            txt = f"{res.status}: {res.steps} steps, {mass:.2f} g ({vol:.2f} mL)."
            if pc is not None:
                if pc.reliable and pc.method == "marker":
                    txt += f" Plunger marker moved {pc.travel_px:.1f} px."
                    if calibrate and pc.travel_px >= config.PLUNGER_MIN_PX and cmd_ml > 0.05:
                        px = pc.travel_px / cmd_ml
                        self.q.put(("suggest_px", px))
                        txt += f" Suggested PLUNGER_PX_PER_ML = {px:.2f} (current {config.PLUNGER_PX_PER_ML:g})."
                else:
                    txt += " Plunger marker not tracked (check PLUNGER_ROI and the tape)."
            if calibrate:
                ref_ml = ref / config.DENSITY_G_PER_ML if ref else vol
                if res.steps > 0 and ref_ml > 0.05:
                    sug = res.steps / ref_ml
                    self.q.put(("suggest", sug))
                    txt += f" Suggested STEPS_PER_ML = {sug:.1f} (current {config.STEPS_PER_ML:g})."
                else:
                    txt += " No flow measured, cannot calibrate."
            return txt
        self.hw_op(fn)

    def apply_suggest_px(self):
        if self.suggest_px:
            self.set_vars["PLUNGER_PX_PER_ML"].set(f"{self.suggest_px:.2f}")
            self.save_settings()
            self.b_apply_px.config(state="disabled")

    def apply_suggest(self):
        if self.suggest:
            self.set_vars["STEPS_PER_ML"].set(f"{self.suggest:.1f}")
            self.save_settings()
            self.b_apply.config(state="disabled")

    # ================= camera tab =================
    def cam_op(self, fn):
        if self.vision is None:
            return self.say("Connect the camera first (top bar).")

        def run():
            try:
                self.q.put(("cammsg", fn()))
            except Exception as e:
                self.q.put(("cammsg", f"Error: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def cam_ref(self):
        def fn():
            q = self.vision.calibrate_empty()
            return (f"Empty holder looks good: image OK (light {q.brightness:.0f}, sharpness {q.sharpness:.1f}), "
                    "no silhouette in the container area. Now place the container and press 'Set target here'.")
        self.cam_op(fn)

    def cam_target(self):
        def fn():
            x, y = self.vision.set_target_here()
            config.save_settings({"CONTAINER_TARGET": [x, y]})
            self.q.put(("fill",))
            return f"Target spot saved: x {x:.3f}, y {y:.3f} (the yellow cross). Allowed offset: {config.CONTAINER_TOL_PX:g} px."
        self.cam_op(fn)

    def cam_test(self):
        def fn():
            r = self.vision.check_container()
            if not r.reliable:
                return f"Image UNRELIABLE ({r.quality.reason}). A run would continue on the scale alone. Fix the light/focus."
            if not r.present:
                return "No container silhouette in the container area."
            verdict = "IN POSITION" if r.in_position else "MISPLACED" + (" (touching the edge of the search area)" if r.clipped else "")
            return f"Container {verdict}: {r.summary()}; tolerance {config.CONTAINER_TOL_PX:g} px."
        self.cam_op(fn)

    def cam_snap(self):
        def fn():
            os.makedirs(os.path.join(HERE, "snapshots"), exist_ok=True)
            p = os.path.join(HERE, "snapshots", time.strftime("%Y%m%d_%H%M%S") + ".jpg")
            return f"Saved {p}" if self.vision.snapshot(p) else "No real camera image to save (simulation)."
        self.cam_op(fn)

    # ================= log / settings =================
    def open_logs(self):
        path = os.path.join(HERE, "logs")
        os.makedirs(path, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception:
            self.say(f"Logs are in {path}")

    def fill_settings(self):
        for key, _, kind in SETTING_FIELDS:
            val = getattr(config, key)
            self.set_vars[key].set(",".join(f"{x:g}" for x in val) if kind in ("roi", "pair") else str(val))

    def save_settings(self):
        new = {}
        try:
            for key, _, kind in SETTING_FIELDS:
                raw = self.set_vars[key].get().strip()
                if kind == "float":
                    new[key] = float(raw)
                elif kind == "int":
                    new[key] = int(raw)
                elif kind in ("roi", "pair"):
                    n = 4 if kind == "roi" else 2
                    vals = [float(x) for x in raw.split(",")]
                    if len(vals) != n or not all(0 <= x <= 1 for x in vals):
                        raise ValueError(f"{key} needs {n} numbers between 0 and 1")
                    new[key] = vals
                else:
                    new[key] = raw
            if new["CAMERA_TYPE"] not in ("picamera", "usb"):
                raise ValueError("camera type must be picamera or usb")
            if new["PLUNGER_MARKER"] not in ("bright", "dark"):
                raise ValueError("plunger marker must be bright or dark")
        except ValueError as e:
            return messagebox.showerror("Settings", f"Invalid value: {e}")
        config.save_settings(new)
        self.say("Settings saved. Reconnect the camera if you changed its type.")

    def reset_settings(self):
        if messagebox.askyesno("Settings", "Reset all settings to the defaults?"):
            config.reset_settings()
            self.fill_settings()
            self.say("Settings reset.")

    # ================= background threads =================
    def mass_loop(self):
        while True:
            time.sleep(0.5)
            link = self.link
            if link is None or not self.hw_lock.acquire(blocking=False):
                continue
            try:
                self.q.put(("mass", link.read_mass()))
            except Exception:
                pass
            finally:
                self.hw_lock.release()

    def preview_loop(self):
        while True:
            time.sleep(0.5)
            v = self.vision
            if v is None or isinstance(v, (MockVision, NullVision)):
                continue
            try:
                self.q.put(("png", v.preview_png((CAM_W, CAM_H))))
            except Exception:
                pass

    # ================= UI updates (main thread only) =================
    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                k = m[0]
                if k == "msg":
                    self.instr.config(text=m[1])
                elif k == "status":
                    self.say(m[1])
                elif k == "hwmsg":
                    self.hw_msg.config(text=m[1])
                elif k == "cammsg":
                    self.cam_msg.config(text=m[1])
                elif k == "fill":
                    self.fill_settings()
                elif k == "conn":
                    self.refresh_conn()
                elif k == "need_continue":
                    self.b_cont.config(state="normal" if m[1] else "disabled")
                elif k == "mass":
                    self.hw_mass.config(text=f"{m[1]:.2f} g")
                    self.mass_lbl.config(text=f"Scale: {m[1]:.2f} g")
                elif k == "png":
                    self.png = m[1]
                elif k == "prog":
                    _, mass, steps, tg = m
                    self.prog_frac = max(0.0, min(1.0, mass / tg)) if tg else 0.0
                    self.pbar["value"] = self.prog_frac * 100
                    self.mass_lbl.config(text=f"Scale: {mass:.2f} g")
                    self.hw_mass.config(text=f"{mass:.2f} g")
                    self.w_cmd.set(f"{steps / config.STEPS_PER_ML:.2f} mL ({steps})")
                    self.w_wgh.set(f"{mass / config.DENSITY_G_PER_ML:.2f} mL")
                elif k == "suggest":
                    self.suggest = m[1]
                    self.b_apply.config(state="normal")
                elif k == "suggest_px":
                    self.suggest_px = m[1]
                    self.b_apply_px.config(state="normal")
                elif k == "row":
                    self.on_row(m[1], m[2], m[3])
                elif k == "log":
                    self.ltree.insert("", "end", values=[m[1].get(c) for c in FIELDS])
                    self.ltree.yview_moveto(1)
                elif k == "done":
                    self.instr.config(text=m[1])
                    if m[2]:
                        s = m[2]
                        txt = f"Accepted {s['accepted']}   Rejected {s['rejected']}   Aborted {s['aborted']}"
                        if s["errors"]:
                            txt += "   |   " + ", ".join(s["errors"])
                        self.summary.config(text=txt)
                    self.b_start.config(state="normal")
                    self.b_pause.config(state="disabled", text="PAUSE")
                    self.b_cont.config(state="disabled")
                    self.b_stop.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def on_row(self, i, status, rec):
        vals = list(self.tree.item(str(i), "values"))
        if rec:
            shown = "ACCEPTED" if status == "ACCEPTED" else rec["error"]
            vals[3] = "-" if rec["weighed_mL"] is None else rec["weighed_mL"]
            vals[4], vals[5] = rec["camera"], shown
            self.counts[status] += 1
            self.w_cmd.set("-" if rec["commanded_mL"] is None else f"{rec['commanded_mL']} mL")
            self.w_wgh.set("-" if rec["weighed_mL"] is None else f"{rec['weighed_mL']} mL")
            detail = [f"offset {rec['container_offset_px']:g} px"] if rec.get("container_offset_px") is not None else []
            if rec.get("plunger_travel_px") is not None:
                detail.append(f"plunger {rec['plunger_travel_px']:g} px")
            self.w_cam.set(rec["camera"] + ("  (" + ", ".join(detail) + ")" if detail else ""))
            self.w_dec.set(shown)
            self.summary.config(text="Accepted: {ACCEPTED}   Rejected: {REJECTED}   Aborted: {ABORTED}".format(**self.counts))
        else:
            vals[5] = status
            self.pbar["value"] = 0
            self.prog_frac = 0.0
            for v in (self.w_cmd, self.w_wgh, self.w_cam, self.w_dec):
                v.set("...")
        self.tree.item(str(i), values=vals, tags=(status,))

    # ---------- camera view (real image or simulated schematic) ----------
    def redraw(self):
        self.draw_view(self.cv_run, VIEW_W, VIEW_H)
        self.draw_view(self.cv_cam, CAM_W, CAM_H)
        self.root.after(500, self.redraw)

    def draw_view(self, cv, w, h):
        cv.delete("all")
        if self.png:                                   # real camera: the picture already carries the overlay
            img = tk.PhotoImage(data=base64.b64encode(self.png))
            if (w, h) != (CAM_W, CAM_H):
                img = img.zoom(w // 160).subsample(CAM_W // 160)    # 480 -> 320 for the Run tab
            cv.img_ref = img
            cv.create_image(0, 0, anchor="nw", image=img)
        elif isinstance(self.vision, MockVision) and self.link is not None:
            self.schematic(cv, w, h)
            for roi, color, name in ((config.CONTAINER_ROI, "#3b8bff", "container"), (config.PLUNGER_ROI, "#ff9a1f", "plunger")):
                x, y, rw, rh = roi
                cv.create_rectangle(x * w, y * h, (x + rw) * w, (y + rh) * h, outline=color, width=2)
                cv.create_text(x * w + 4, y * h + 2, text=name, fill=color, anchor="nw", font=("TkDefaultFont", 8))
        else:
            cv.create_text(w / 2, h / 2, text="No camera view\n(connect the camera or use Simulation)", fill="#aaa", justify="center")

    def schematic(self, cv, w, h):
        link = self.link
        f = w / 320.0                                  # the drawing below is laid out for 320 x 240
        R = lambda x0, y0, x1, y1, **kw: cv.create_rectangle(x0 * f, y0 * f, x1 * f, y1 * f, **kw)
        fault = getattr(link, "fault", "none")
        cv.create_rectangle(0, 0, w, h, fill="#2b2b2b", outline="")
        cv.create_text(8, 6, text="SIMULATED CAMERA VIEW", fill="#888", anchor="nw", font=("TkDefaultFont", 8))
        if fault == "dark":
            cv.create_text(w / 2, 24 * f, text="IMAGE TOO DARK: camera unreliable", fill="#ff6a6a", font=("TkDefaultFont", 10, "bold"))
        py = 25 + 50 * self.prog_frac
        R(130, 25, 190, 105, outline="white", width=2)
        R(152, 10, 168, py + 5, fill="#bbb", outline="")
        cv.create_oval(158 * f - 5, (py - 3) * f - 5, 158 * f + 5, (py - 3) * f + 5, outline="#ff9a1f", width=2)   # plunger marker
        present = getattr(link, "raw", 0) > config.CONTAINER_MIN_G
        if present:
            sx = 40 if fault == "misplaced" else 0     # a misplaced container sits off the target
            R(110 + sx, 140, 210 + sx, 225, outline="#ff5a5a" if sx else "white", width=2)
            if getattr(link, "offset", 0):
                level = 83 * self.prog_frac
                R(112 + sx, 224 - level, 208 + sx, 224, fill="#4aa3ff", outline="")

    def close(self):
        self.stop_flag.set()
        self.disconnect_all()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
