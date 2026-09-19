import os
import json
import webview

import sys

if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, '_MEIPASS', EXE_DIR)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = EXE_DIR

html_path = os.path.join(BUNDLE_DIR, "index.html")
if not os.path.exists(html_path):
    html_path = os.path.join(EXE_DIR, "index.html")

config_path = os.path.join(EXE_DIR, "config.json")
folders_path = os.path.join(EXE_DIR, "folders.json")

DEFAULT_FOLDERS = {
    "general": [
        {
            "id": "main",
            "name": "مجلد المنهج والمواد الرئيسي",
            "category": "عام",
            "icon": "📁",
            "path": "C:\\IT_Materials",
            "description": "المجلد الأساسي الذي يحتوي على كافة ملفات ومجلدات المادة التعليمية"
        },
        {
            "id": "books",
            "name": "مجلد الكتب والمناهج الدراسية",
            "category": "PDF",
            "icon": "📕",
            "path": "C:\\IT_Materials\\الكتب",
            "description": "كتب الطالب، أدلة المعلم، والكتب الإثرائية بصيغة PDF"
        },
        {
            "id": "exams",
            "name": "مجلد الاختبارات وبنوك الأسئلة",
            "category": "Word",
            "icon": "📝",
            "path": "C:\\IT_Materials\\الاختبارات",
            "description": "نماذج الاختبارات الفترية والنهائية وبنوك الأسئلة بصيغة DOCX"
        },
        {
            "id": "grades",
            "name": "مجلد سجلات وكشوف درجات الطلاب",
            "category": "Excel",
            "icon": "📊",
            "path": "C:\\IT_Materials\\سجلات الدرجات",
            "description": "سجلات رصد الدرجات اليومية والفترية وكشوف الرصد بصيغة XLSX"
        },
        {
            "id": "interactive",
            "name": "مجلد الأنشطة التفاعلية والبرمجيات",
            "category": "HTML",
            "icon": "🌐",
            "path": "C:\\IT_Materials\\الأنشطة التفاعلية",
            "description": "الأنشطة التفاعلية ومحاكاة الشبكات والتطبيقات التعليمية"
        },
        {
            "id": "worksheets",
            "name": "مجلد أوراق العمل والأنشطة",
            "category": "عام",
            "icon": "📄",
            "path": "C:\\IT_Materials\\أوراق العمل",
            "description": "أوراق العمل الإثرائية والعلاجية للدروس المختلفة"
        }
    ],
    "sections": [
        {
            "id": "sec_g6_a",
            "grade": "الصف السادس",
            "section": "شعبة أ",
            "path": "C:\\IT_Materials\\الصف السادس\\شعبة أ"
        },
        {
            "id": "sec_g6_b",
            "grade": "الصف السادس",
            "section": "شعبة ب",
            "path": "C:\\IT_Materials\\الصف السادس\\شعبة ب"
        },
        {
            "id": "sec_g6_c",
            "grade": "الصف السادس",
            "section": "شعبة ج",
            "path": "C:\\IT_Materials\\الصف السادس\\شعبة ج"
        },
        {
            "id": "sec_g7_a",
            "grade": "الصف السابع",
            "section": "شعبة أ",
            "path": "C:\\IT_Materials\\الصف السابع\\شعبة أ"
        },
        {
            "id": "sec_g7_b",
            "grade": "الصف السابع",
            "section": "شعبة ب",
            "path": "C:\\IT_Materials\\الصف السابع\\شعبة ب"
        },
        {
            "id": "sec_g9_a",
            "grade": "الصف التاسع",
            "section": "شعبة أ",
            "path": "C:\\IT_Materials\\الصف التاسع\\شعبة أ"
        },
        {
            "id": "sec_g9_b",
            "grade": "الصف التاسع",
            "section": "شعبة ب",
            "path": "C:\\IT_Materials\\الصف التاسع\\شعبة ب"
        },
        {
            "id": "sec_g9_c",
            "grade": "الصف التاسع",
            "section": "شعبة ج",
            "path": "C:\\IT_Materials\\الصف التاسع\\شعبة ج"
        }
    ],
    "custom": []
}

def load_config():
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
    return {
        "lessons": [],
        "files": [],
        "currentGrade": "الصف السادس",
        "currentSheba": "شعبة أ",
        "settings": {"theme": "dark", "language": "ar"}
    }

def save_config(config):
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

def load_folders():
    try:
        if os.path.exists(folders_path):
            with open(folders_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading folders: {e}")
    
    # If not exists or invalid, save and return default
    save_folders(DEFAULT_FOLDERS)
    return DEFAULT_FOLDERS

def save_folders(folders):
    try:
        with open(folders_path, 'w', encoding='utf-8') as f:
            json.dump(folders, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving folders: {e}")
        return False

class AppApi:
    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def load_folders(self):
        return load_folders()

    def save_folders(self, folders):
        success = save_folders(folders)
        return {"success": success, "error": None if success else "فشل حفظ ملف folders.json"}

    def load_config(self):
        return load_config()

    def save_config(self, config):
        success = save_config(config)
        return {"success": success, "error": None if success else "فشل حفظ ملف config.json"}

    def select_folder(self, initial_dir=""):
        try:
            if not self._window:
                return {"success": False, "path": None, "error": "النافذة غير جاهزة"}
            
            directory = initial_dir if initial_dir and os.path.isdir(initial_dir) else ""
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=directory
            )
            if result and len(result) > 0:
                return {"success": True, "path": result[0]}
            return {"success": False, "path": None, "cancelled": True}
        except Exception as e:
            return {"success": False, "path": None, "error": str(e)}

    def check_folder_exists(self, folder_path):
        if not folder_path:
            return False
        return os.path.isdir(folder_path)

    def open_folder(self, folder_path):
        try:
            if folder_path and os.path.exists(folder_path):
                os.startfile(folder_path)
                return {"success": True}
            return {"success": False, "error": "المجلد غير موجود على هذا المسار"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_folder(self, folder_path):
        try:
            if not folder_path:
                return {"success": False, "error": "المسار فارغ"}
            os.makedirs(folder_path, exist_ok=True)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_file(self, file_path):
        try:
            if file_path and os.path.exists(file_path):
                os.startfile(file_path)
                return {"success": True}
            return {"success": False, "error": "الملف غير موجود"}
        except Exception as e:
            return {"success": False, "error": str(e)}

def start_app():
    api = AppApi()
    
    window = webview.create_window(
        title="متابعة مادة تقنية المعلومات",
        width=1240,
        height=840,
        url=f"file://{html_path}",
        resizable=True,
        text_direction="rtl",
        js_api=api
    )
    api.set_window(window)
    
    webview.start()

if __name__ == "__main__":
    start_app()