#include <string>
#include <iostream>
#include <fstream>
#include <vector>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <chrono>
#include <ctime>
#include <iomanip>
#include <algorithm>
#include <atomic>
#include "ForexConnect.h"
#include <cstdlib>


// ===== CREDENCIALES =====
static const char* FX_USER = "D161650647";
static const char* FX_PASS = "gyC8j";
static const char* FX_CONN = "Demo"; // o "Real"
static const char* FX_URL  = "http://www.fxcorporate.com/Hosts.jsp";
// ========================

// Convierte OLE Automation Date a ISO-8601 UTC
static std::string oaDateToISO8601(double oa){
    double secs = (oa - 25569.0) * 86400.0;
    std::time_t t = (std::time_t)secs;
    std::tm *gmt = std::gmtime(&t);
    char buf[32];
    std::strftime(buf,sizeof(buf),"%Y-%m-%d %H:%M:%S", gmt);
    return std::string(buf);
}

// --------- Listeners con refcount correcto ----------
struct StatusListener : public IO2GSessionStatus {
    std::atomic<long> ref{1};
    IO2GSession* s; bool connected=false, disconnected=false, failed=false;
    explicit StatusListener(IO2GSession* ss): s(ss) {}
    void onSessionStatusChanged(O2GSessionStatus st) override {
        if (st==Connected)    { connected=true;    std::cout<<"[INFO] Connected\n"; }
        if (st==Disconnected) { disconnected=true; std::cout<<"[INFO] Disconnected\n"; }
    }
    void onLoginFailed(const char* err) override {
        failed=true; std::cerr<<"[ERR] Login failed: "<<(err?err:"")<<"\n";
    }
    long addRef() override { return ++ref; }
    long release() override { long r=--ref; if(r==0) delete this; return r; }
};

struct CSVRow { double d; double bo,bh,bl,bc, ao,ah,al,ac; int vol; };

// Listener que COPIA las velas dentro del callback (seguro)
struct CollectingResponseListener : public IO2GResponseListener {
    std::atomic<long> ref{1};
    IO2GSession* session;
    std::string reqId;
    std::vector<CSVRow> rows;
    std::string error;
    std::mutex m; std::condition_variable cv; bool done=false;

    explicit CollectingResponseListener(IO2GSession* s): session(s) {}

    void setRequestID(const char* id){ reqId = id? id:""; }

    void onRequestCompleted(const char* id, IO2GResponse* r) override {
        if (!(id && reqId==id)) return;
        O2G2Ptr<IO2GResponseReaderFactory> rr = session->getResponseReaderFactory();
        if (!rr){ std::lock_guard<std::mutex> lk(m); error="No ResponseReaderFactory"; done=true; cv.notify_all(); return; }

        if (r->getType()!=MarketDataSnapshot){
            std::lock_guard<std::mutex> lk(m);
            error = "Unexpected response type";
            done = true; cv.notify_all(); return;
        }

        O2G2Ptr<IO2GMarketDataSnapshotResponseReader> candles = rr->createMarketDataSnapshotReader(r);
        if (!candles){ std::lock_guard<std::mutex> lk(m); error="No snapshot reader"; done=true; cv.notify_all(); return; }

        const int n = candles->size();
        std::vector<CSVRow> tmp; tmp.reserve(n);
        for(int i=0;i<n;++i){
            tmp.push_back(CSVRow{
                candles->getDate(i),
                candles->getBidOpen(i),  candles->getBidHigh(i),  candles->getBidLow(i),  candles->getBidClose(i),
                candles->getAskOpen(i),  candles->getAskHigh(i),  candles->getAskLow(i),  candles->getAskClose(i),
                candles->getVolume(i)
            });
        }

        {
            std::lock_guard<std::mutex> lk(m);
            rows.swap(tmp);           // <<--- copiamos a memoria PROPIA
            done = true;
        }
        cv.notify_all();
    }

    void onRequestFailed(const char* id, const char* err) override {
        if (!(id && reqId==id)) return;
        std::lock_guard<std::mutex> lk(m);
        error = err?err:"request failed";
        done = true; cv.notify_all();
    }

    void onTablesUpdates(IO2GResponse*) override {}

    bool wait_ms(int ms){
        std::unique_lock<std::mutex> lk(m);
        return cv.wait_for(lk,std::chrono::milliseconds(ms),[&]{return done;});
    }

    long addRef() override { return ++ref; }
    long release() override { long r=--ref; if(r==0) delete this; return r; }
};
// ---------------------------------------------------

static bool ensureInstrument(IO2GSession* session, const std::string& symbol){
    O2G2Ptr<IO2GLoginRules> rules = session->getLoginRules();
    if (!rules) { std::cerr<<"[ERR] No LoginRules.\n"; return false; }
    O2G2Ptr<IO2GResponse> offersResp = rules->getTableRefreshResponse(Offers);
    if (!offersResp) { std::cerr<<"[ERR] No OffersResponse.\n"; return false; }
    O2G2Ptr<IO2GResponseReaderFactory> rr = session->getResponseReaderFactory();
    if (!rr) { std::cerr<<"[ERR] No ResponseReaderFactory.\n"; return false; }
    O2G2Ptr<IO2GOffersTableResponseReader> offers = rr->createOffersTableReader(offersResp);
    if (!offers) { std::cerr<<"[ERR] No OffersReader.\n"; return false; }

    bool found=false;
    for (int i=0;i<offers->size();++i){
        IO2GOfferRow* row = offers->getRow(i);
        if (row && symbol == row->getInstrument()) { found=true; break; }
    }
    if (!found){
        std::cerr<<"[ERR] Símbolo '"<<symbol<<"' no disponible. Ejemplos:\n";
        for (int i=0;i<std::min(offers->size(), 15); ++i){
            IO2GOfferRow* row = offers->getRow(i);
            if (row) std::cerr<<" - "<<row->getInstrument()<<"\n";
        }
    }
    return found;
}

static void disable_sanitizers_runtime(){
	//desactiva reporte de fugas de memoria la salir
	setenv("ASAN_OPTIONS", "detect_leaks=0", 1);
	setenv("LSAN_OPTIONS", "detect_leaks=0", 1);
}

int main(int argc, char** argv){
    disable_sanitizers_runtime();
    std::string symbol   = (argc>1? argv[1] : "EUR/USD");
    std::string tf_name  = (argc>2? argv[2] : "m5");      // m1,m5,m15,m30,h1,h4,D1...
    int bars             = (argc>3? std::max(1, std::atoi(argv[3])) : 300);
    std::string out_csv  = (argc>4? argv[4] : "candles.csv");

    std::cout << "[INFO] Symbol="<<symbol<<" TF="<<tf_name<<" Bars="<<bars<<"\n";

    IO2GSession* session = CO2GTransport::createSession();
    auto st = new StatusListener(session);
    session->subscribeSessionStatus(st);
    session->login(FX_USER, FX_PASS, FX_URL, FX_CONN);

    for (int i=0;i<200 && !st->connected && !st->failed;i++)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    if (!st->connected){
        std::cerr<<"[ERR] No conectado.\n";
        session->unsubscribeSessionStatus(st); st->release();
        session->release();
        return 2;
    }

    auto cleanup = [&](){
        session->logout();
        for (int i=0;i<100 && !st->disconnected;i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        session->unsubscribeSessionStatus(st); st->release();
        session->release();
    };

    O2G2Ptr<IO2GRequestFactory> rf = session->getRequestFactory();
    if (!rf){ std::cerr<<"[ERR] No RequestFactory.\n"; cleanup(); return 3; }

    O2G2Ptr<IO2GTimeframeCollection> tfs = rf->getTimeFrameCollection();
    if (!tfs){ std::cerr<<"[ERR] No TimeframeCollection.\n"; cleanup(); return 4; }
    O2G2Ptr<IO2GTimeframe> tf = tfs->get(tf_name.c_str());
    if (!tf){
        std::cerr<<"[ERR] Timeframe '"<<tf_name<<"' no válido. Disponibles:\n";
        for (int i=0;i<tfs->size();++i){ IO2GTimeframe* t=tfs->get(i); if (t) std::cerr<<" - "<<t->getID()<<"\n"; }
        cleanup(); return 5;
    }

    if (!ensureInstrument(session, symbol)){ cleanup(); return 6; }

    O2G2Ptr<IO2GRequest> req = rf->createMarketDataSnapshotRequestInstrument(symbol.c_str(), tf, bars);
    if (!req){ std::cerr<<"[ERR] No se pudo crear request de snapshot.\n"; cleanup(); return 7; }

    auto rl = new CollectingResponseListener(session);
    rl->setRequestID(req->getRequestID());
    session->subscribeResponse(rl);
    session->sendRequest(req);
    if (!rl->wait_ms(15000)){
        std::cerr<<"[ERR] Timeout esperando histórico.\n";
        session->unsubscribeResponse(rl); rl->release(); cleanup(); return 8;
    }
    if (!rl->error.empty()){
        std::cerr<<"[ERR] Request: "<<rl->error<<"\n";
        session->unsubscribeResponse(rl); rl->release(); cleanup(); return 9;
    }

    // Ya tenemos una COPIA en rl->rows (segura). Podemos desuscribir y liberar el listener aquí.
    session->unsubscribeResponse(rl);
    std::vector<CSVRow> rows;
    {
        // movemos la copia fuera y liberamos rl
        std::lock_guard<std::mutex> lk(rl->m);
        rows = std::move(rl->rows);
    }
    rl->release();

    if (rows.empty()){ std::cerr<<"[ERR] Sin datos.\n"; cleanup(); return 10; }

    std::sort(rows.begin(), rows.end(), [](const CSVRow& a, const CSVRow& b){ return a.d < b.d; });

    std::ofstream f(out_csv);
    if (!f){ std::cerr<<"[ERR] No se pudo crear "<<out_csv<<"\n"; cleanup(); return 11; }
    f << "time_utc,bid_open,bid_high,bid_low,bid_close,ask_open,ask_high,ask_low,ask_close,volume\n";
    f.setf(std::ios::fixed); f<<std::setprecision(6);
    for(const auto& r: rows){
        f << oaDateToISO8601(r.d) << ","
          << r.bo << "," << r.bh << "," << r.bl << "," << r.bc << ","
          << r.ao << "," << r.ah << "," << r.al << "," << r.ac << ","
          << r.vol << "\n";
    }
    f.close();

    std::cout << "[OK] CSV escrito: " << out_csv << " (filas="<<rows.size()<<")\n";
    cleanup();
    return 0;
}
