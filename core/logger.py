from datetime import datetime
import os
os.system('color')

class logger:
    def __init__(self):
        self.WHITE: str = "\u001b[37m"
        self.MAGENTA: str = "\033[38;5;97m"
        self.MAGENTAA: str = "\033[38;2;157;38;255m"
        self.RED: str = "\033[38;5;196m"
        self.GREEN: str = "\033[38;5;40m"
        self.YELLOW: str = "\033[38;5;220m"
        self.BLUE: str = "\033[38;5;21m"
        self.PINK: str = "\033[38;5;176m"
        self.CYAN: str = "\033[96m"
        self.GRAY: str = "\033[38;5;244m"

    def get_time(self) -> str:
        return datetime.now().strftime("%H:%M:%S")
    
    def message(self, level: str, message: str, start: int = None, end: int = None) -> str:
        time_now = f" {self.PINK}[{self.MAGENTA}{self.get_time()}{self.PINK}] {self.WHITE}|"
        timer = f" {self.MAGENTAA}In{self.WHITE} -> {self.MAGENTAA}{str(end - start)[:5]} Sec" if start and end else ""
        return f"{time_now} {self.PINK}[{level}{self.PINK}] {self.WHITE}-> {self.PINK}[{self.MAGENTA}{message}{self.PINK}]{timer}"
    
    def success(self, message: str, start: int = None, end: int = None, level: str = "Success") -> None:
        print(self.message(f"{self.GREEN}{level}", f"{self.GREEN}{message}", start, end))

    def info(self, message: str, start: int = None, end: int = None, level: str = "Info") -> None:
        print(self.message(f"{self.BLUE}{level}", f"{self.BLUE}{message}", start, end))

    def failure(self, message: str, start: int = None, end: int = None, level: str = "Failure") -> None:
        print(self.message(f"{self.RED}{level}", f"{self.RED}{message}", start, end))
    
    def warning(self, message: str, start: int = None, end: int = None, level: str = "Warning") -> None:
        print(self.message(f"{self.YELLOW}{level}", f"{self.YELLOW}{message}", start, end))
    
    def captcha(self, message: str, start: int = None, end: int = None, level: str = "hCaptcha") -> None:
        print(self.message(f"{self.CYAN}{level}", f"{self.CYAN}{message}", start, end))

    def debug(self, message: str, start: int = None, end: int = None, level: str = "Debug") -> None:
        print(self.message(f"{self.GRAY}{level}", f"{self.GRAY}{message}", start, end))

log = logger()