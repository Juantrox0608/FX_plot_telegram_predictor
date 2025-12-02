Bot para predicciones de telegram:
FX -> csv -> DNN -> html/PDF -> tg

librerias python:
os , re , shlex, subprocess .shutil,datetime,pathlib,dotenv 

APIS python :
Telegram

si se sigue ejecutando en ubuntu : 
# Puedes dar FX_TO_CSV absoluto en .env.
# Si además pones FX_BIN_DIR, aquí lo usamos solo si FX_TO_CSV no está.
FX_BIN_DIR = os.getenv("FX_BIN_DIR", "/opt/bin").strip()
FX_TO_CSV  = os.getenv("FX_TO_CSV", "").strip() or os.path.join(FX_BIN_DIR, "fx_to_csv_copy")
# Expande ~ si se usó
FX_TO_CSV = os.path.expanduser(FX_TO_CSV)

# Carpeta donde también puedes guardar copias (opcional)
FX_OUT = Path(os.getenv("FX_OUT", BASE_DIR / "out"))
FX_OUT.mkdir(parents=True, exist_ok=True)

crear todas las direcciones con las carpetas OPT

Librerias C++:
string iostrean thread chrono fstream vector mutex condition_variable ctime iomanip algorithm atomic cstdlib  	
toch sstream unordered_map cmath numeric cmath cstring cairo

APIS C++:
ForexConnect 

Compilar fx_to_csv_copy.cpp


g++ -std=c++17 fx_to_csv_copy.cpp \
  -I"$HOME/ForexConnectAPI/include" \
  -L"$HOME/ForexConnectAPI/lib" \
  -Wl,-rpath,"$HOME/ForexConnectAPI/lib" \
  -lForexConnect -lpthread \
  -o fx_to_csv_copy

