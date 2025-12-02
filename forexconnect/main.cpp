
#include <string>
#include <iostream>
#include <thread>
#include <chrono>
#include "ForexConnect.h"


//========Credenciales================

static const char* FX_USER = "D161648561";
static const char* FX_PASS = "pU8fy";
static const char* FX_CONN = "Demo";
static const char* FX_URL  = "http://www.fxcorporate.com/Hosts.jsp";

//========================================

// Listener mínimo de estado de sesión
struct StatusListener : public IO2GSessionStatus {
    IO2GSession* mSession;
    bool connected=false, disconnected=false, failed=false;

    explicit StatusListener(IO2GSession* s): mSession(s) {}

    void onSessionStatusChanged(O2GSessionStatus status) override {
        if (status == Connected)   { connected   = true;  std::cout << "Estado: Connected\n"; }
        if (status == Disconnected){ disconnected= true;  std::cout << "Estado: Disconnected\n"; }
    }
    void onLoginFailed(const char* error) override {
        failed = true; std::cerr << "Login failed: " << (error?error:"(sin mensaje)") << "\n";
    }

    // Contadores dummy para la interfaz COM-like de la API
    long addRef() override { return 1; }
    long release() override { return 1; }
};

int main() {
    // Crea la sesión y suscribe el listener
    IO2GSession* session = CO2GTransport::createSession();
    StatusListener st(session);
    session->subscribeSessionStatus(&st);

    std::cout << "Haciendo login contra " << FX_URL << " (" << FX_CONN << ")...\n";
    session->login(FX_USER, FX_PASS, FX_URL, FX_CONN);

    // Espera a que conecte o falle (hasta ~5s)
    for (int i = 0; i < 100 && !st.connected && !st.failed; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));

    if (!st.connected) {
        std::cerr << "No se logró conectar. Revisa USER/PASS/CONN/URL o si el mercado está cerrado.\n";
        session->unsubscribeSessionStatus(&st);
        session->release();
        return 2;
    }

    std::cout << "Conectado. Cerrando sesión...\n";
    session->logout();

    // Espera desconexión limpia
    for (int i = 0; i < 100 && !st.disconnected; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(20));

    session->unsubscribeSessionStatus(&st);
    session->release();
    std::cout << "Listo.\n";
    return 0;
}

