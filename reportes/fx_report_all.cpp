#include <torch/torch.h>
#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <unordered_map>
#include <algorithm>
#include <numeric>
#include <cmath>
#include <iomanip>
#include <cstring>
#include <cstdlib>

// Cairo para PNG (sin Pango)
#include <cairo/cairo.h>

// ============== Helper: noticias desde el ÚLTIMO argumento ==================
// Acepta:
//   • "10100"   (5+ dígitos 0/1; se toman 5 primeros)
//   • "1,0,1,0,0" (5 valores separados por comas)
static bool parse_news_from_last_arg(int argc, char** argv,
                                     int& n1, int& n2, int& n3, int& n4, int& n5)
{
    if (argc < 2) return false;
    const char* s = argv[argc - 1];
    if (!s || !*s) return false;

    // "10100"
    size_t len = std::strlen(s);
    bool only01 = (std::strspn(s, "01") == len);
    if (only01 && len >= 5) {
        n1 = s[0] - '0';
        n2 = s[1] - '0';
        n3 = s[2] - '0';
        n4 = s[3] - '0';
        n5 = s[4] - '0';
        return true;
    }
    // "1,0,1,0,0"
    const char* p = s;
    const char* comma = std::strchr(p, ',');
    if (comma) {
        int v[5] = {0,0,0,0,0};
        int i = 0;
        while (*p && i < 5) {
            v[i++] = std::atoi(p);
            const char* c = std::strchr(p, ',');
            if (!c) break;
            p = c + 1;
        }
        if (i == 5) { n1=v[0]; n2=v[1]; n3=v[2]; n4=v[3]; n5=v[4]; return true; }
    }
    return false;
}

// =================== Utilidades ===================
static std::string now_iso(){
    std::time_t t=std::time(nullptr);
    std::tm tm{};
    gmtime_r(&t,&tm);
    char buf[32];
    std::strftime(buf,sizeof(buf),"%Y-%m-%d %H:%M:%S UTC",&tm);
    return buf;
}
static std::string fmt6(double v){
    if(!std::isfinite(v)) return "-";
    std::ostringstream o; o.setf(std::ios::fixed); o<<std::setprecision(6)<<v; return o.str();
}
static std::vector<std::string> split(const std::string& s, char c){
    std::vector<std::string> v; std::stringstream ss(s); std::string tok;
    while(std::getline(ss,tok,c)) v.push_back(tok);
    return v;
}

// =================== CSV ===================
struct Series {
    std::vector<std::string> t;
    std::vector<double> open, high, low, close, volume;
};
static bool read_csv(const std::string& path, Series& S){
    std::ifstream f(path);
    if(!f){ std::cerr<<"No pude abrir "<<path<<"\n"; return false; }

    std::string header;
    if(!std::getline(f, header)){ std::cerr<<"CSV vacío\n"; return false; }

    auto cols = split(header, ',');
    std::unordered_map<std::string,int> idx;
    for (int i=0; i<(int)cols.size(); ++i){
        std::string k = cols[i];
        // quita espacios y comillas del header
        k.erase(std::remove_if(k.begin(), k.end(),
               [](unsigned char c){ return std::isspace(c) || c=='\"'; }), k.end());
        std::transform(k.begin(), k.end(), k.begin(),
               [](unsigned char c){ return std::tolower(c); });
        idx[k] = i;
    }

    auto pick = [&](std::initializer_list<const char*> keys)->int{
        for (const char* key : keys){
            auto it2 = idx.find(key);
            if (it2 != idx.end()) return it2->second;
        }
        return -1;
    };

    int it = pick({"time_utc","time","datetime","timestamp"});
    int iO = pick({"open","bid_open","ask_open","bidopen","openbid"});
    int iH = pick({"high","bid_high","ask_high","bidhigh","highbid"});
    int iL = pick({"low","bid_low","ask_low","bidlow","lowbid"});
    int iC = pick({"close","bid_close","ask_close","bidclose","closebid"});
    int iV = pick({"volume","tickqty","tick_volume","vol"});

    if (it<0 || iO<0 || iH<0 || iL<0 || iC<0){
        std::cerr<<"CSV debe tener time_utc y open/high/low/close o equivalentes bid_/ask_.\n";
        return false;
    }

    std::string line;
    while (std::getline(f, line)){
        if(line.empty()) continue;
        auto v = split(line, ',');
        if ((int)v.size() <= std::max({it, iO, iH, iL, iC})) continue;

        S.t.push_back(v[it]);
        S.open.push_back(std::atof(v[iO].c_str()));
        S.high.push_back(std::atof(v[iH].c_str()));
        S.low.push_back(std::atof(v[iL].c_str()));
        S.close.push_back(std::atof(v[iC].c_str()));
        double vol = 0.0;
        if (iV >= 0 && iV < (int)v.size()) vol = std::atof(v[iV].c_str());
        S.volume.push_back(vol);
    }
    return !S.close.empty();
}

// =================== Indicadores ===================
static std::vector<double> SMA(const std::vector<double>& x, int n){
    std::vector<double> y(x.size(), NAN);
    if(n<=0) return y;
    double s=0; int m=0;
    for(size_t i=0;i<x.size();++i){
        s+=x[i]; m++;
        if(m>n){ s-=x[i-n]; m--; }
        if(m==n) y[i]=s/n;
    }
    return y;
}
static std::vector<double> EMA(const std::vector<double>& x, int n){
    std::vector<double> y(x.size(), NAN);
    if(n<=0 || x.empty()) return y;
    double k=2.0/(n+1.0), e=x[0]; y[0]=e;
    for(size_t i=1;i<x.size();++i){ e=x[i]*k + e*(1.0-k); y[i]=e; }
    return y;
}
static std::vector<double> RSI(const std::vector<double>& x, int n){
    std::vector<double> y(x.size(), NAN);
    if(n<=0 || x.size()<2) return y;
    double gain=0, loss=0;
    for(int i=1;i<=n && i<(int)x.size();++i){
        double d=x[i]-x[i-1];
        if(d>=0) gain+=d; else loss-=d;
    }
    double rs=(loss==0?0:gain/loss);
    y[n]=(loss==0?100:100-100/(1+rs));
    double alpha=1.0/n;
    for(size_t i=n+1;i<x.size();++i){
        double d=x[i]-x[i-1];
        double g=d>0?d:0, l=d<0?-d:0;
        gain=(1-alpha)*gain+alpha*g;
        loss=(1-alpha)*loss+alpha*l;
        rs=(loss==0?0:gain/loss);
        y[i]=(loss==0?100:100-100/(1+rs));
    }
    return y;
}
static void MACD(const std::vector<double>& x, int fast, int slow, int sig,
                 std::vector<double>& m, std::vector<double>& s, std::vector<double>& h){
    auto ef=EMA(x,fast), es=EMA(x,slow);
    m.assign(x.size(), NAN);
    for(size_t i=0;i<x.size();++i) if(!std::isnan(ef[i]) && !std::isnan(es[i])) m[i]=ef[i]-es[i];
    s=EMA(m,sig);
    h.assign(x.size(), NAN);
    for(size_t i=0;i<x.size();++i) if(!std::isnan(m[i]) && !std::isnan(s[i])) h[i]=m[i]-s[i];
}
static void Boll(const std::vector<double>& c, std::vector<double>& mid, std::vector<double>& up, std::vector<double>& lo, int n=20, double k=2.0){
    mid=SMA(c,n);
    up.assign(c.size(),NAN); lo.assign(c.size(),NAN);
    for(size_t i=0;i<c.size();++i){
        if(i+1<(size_t)n) continue;
        double m=mid[i]; if(std::isnan(m)) continue;
        double s=0; for(int j=0;j<n;j++){ double d=c[i-j]-m; s+=d*d; }
        double sd=std::sqrt(s/n);
        up[i]=m+k*sd; lo[i]=m-k*sd;
    }
}

// =================== Modelo GRU simple ===================
struct GRUModelImpl : torch::nn::Module{
    torch::nn::GRU gru{nullptr};
    torch::nn::Linear fc{nullptr};
    GRUModelImpl(int in, int hid, int nl, int out){
        gru=register_module("gru", torch::nn::GRU(torch::nn::GRUOptions(in,hid).num_layers(nl).batch_first(true)));
        fc =register_module("fc",  torch::nn::Linear(hid,out));
    }
    torch::Tensor forward(torch::Tensor x){
        auto o=std::get<0>(gru->forward(x)); // [B,T,H]
        auto last=o.index({torch::indexing::Slice(), -1, torch::indexing::Slice()}); // [B,H]
        return fc->forward(last); // [B,out]
    }
};
TORCH_MODULE(GRUModel);

static void make_windows(const std::vector<double>& c, int T, int H, torch::Tensor& X, torch::Tensor& Y, double& mu, double& sd){
    int N=(int)c.size();
    mu=std::accumulate(c.begin(),c.end(),0.0)/std::max(1,N);
    double var=0; for(double v:c){ double d=v-mu; var+=d*d; }
    sd=std::sqrt(var/std::max(1,N-1));
    if(sd<=1e-12) sd=1.0;
    std::vector<double> z=c; for(double& v:z) v=(v-mu)/sd;

    int M = N - (T+H) + 1;
    if(M<=0){ X=torch::empty({0}); Y=torch::empty({0}); return; }
    std::vector<float> xb; xb.reserve(M*T);
    std::vector<float> yb; yb.reserve(M);
    for(int i=0;i<M;i++){
        for(int j=0;j<T;j++) xb.push_back((float)z[i+j]);
        yb.push_back((float)z[i+T+H-1]);
    }
    X=torch::from_blob(xb.data(), {M,T,1}, torch::kFloat32).clone();
    Y=torch::from_blob(yb.data(), {M,1},   torch::kFloat32).clone();
}

// =================== Dibujo PNG (Cairo solo) ===================
static bool draw_png(const std::string& outpng, const std::string& symbol,
                     const Series& S,
                     const std::vector<double>& sma20,
                     const std::vector<double>& bbU,
                     const std::vector<double>& bbL,
                     int pred_score, int tech_score, int news_score, int final_score)
{
    const int W=1100,H=600,PADL=60,PADR=20,PADT=60,PADB=80;
    cairo_surface_t* surf=cairo_image_surface_create(CAIRO_FORMAT_ARGB32,W,H);
    cairo_t* cr=cairo_create(surf);

    // fondo
    cairo_set_source_rgb(cr, 0.10,0.12,0.16); cairo_paint(cr);

    auto draw_text=[&](double x,double y,const std::string& txt,double r=1,double g=1,double b=1){
        cairo_set_source_rgb(cr,r,g,b);
        cairo_move_to(cr,x,y);
        cairo_select_font_face(cr, "Sans", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
        cairo_set_font_size(cr, 12.0);
        cairo_show_text(cr, txt.c_str());
    };

    // encabezado
    std::ostringstream ttl; ttl<<"Indicadores "<<symbol<<"  "<<now_iso();
    draw_text(20,15,ttl.str(),1,1,1);
    std::ostringstream ps; ps<<"Pred: "<<pred_score<<"   Ind: "<<tech_score<<"   News: "<<news_score<<"   Final: "<<final_score;
    draw_text(20,35,ps.str(),0.8,0.9,1.0);

    // marco
    double minv=1e300,maxv=-1e300;
    for(double v: S.close){ minv=std::min(minv,v); maxv=std::max(maxv,v); }
    for(size_t i=0;i<S.close.size();++i){
        if(!std::isnan(bbU[i])) maxv=std::max(maxv, bbU[i]);
        if(!std::isnan(bbL[i])) minv=std::min(minv, bbL[i]);
    }
    if(!(maxv>minv)){ minv=0; maxv=1; }
    int N=S.close.size();
    auto xmap=[&](int i){ return PADL + (double)i*(W-PADL-PADR)/std::max(1,N-1); };
    auto ymap=[&](double v){ return H-PADB - (v-minv)*(H-PADT-PADB)/(maxv-minv); };

    // ejes
    cairo_set_source_rgb(cr, 0.5,0.5,0.5);
    cairo_set_line_width(cr,1);
    cairo_move_to(cr,PADL,PADT); cairo_line_to(cr,PADL,H-PADB); cairo_stroke(cr);
    cairo_move_to(cr,PADL,H-PADB); cairo_line_to(cr,W-PADR,H-PADB); cairo_stroke(cr);

    // bandas
    cairo_set_source_rgb(cr,0.3,0.6,0.9);
    cairo_set_line_width(cr,1);
    bool first=true;
    for(int i=0;i<N;i++){
        if(std::isnan(bbL[i])) continue;
        double x=xmap(i), y=ymap(bbL[i]);
        if(first){ cairo_move_to(cr,x,y); first=false; } else cairo_line_to(cr,x,y);
    }
    cairo_stroke(cr);
    first=true;
    for(int i=0;i<N;i++){
        if(std::isnan(bbU[i])) continue;
        double x=xmap(i), y=ymap(bbU[i]);
        if(first){ cairo_move_to(cr,x,y); first=false; } else cairo_line_to(cr,x,y);
    }
    cairo_stroke(cr);

    // SMA20
    cairo_set_source_rgb(cr,0.9,0.8,0.2);
    first=true;
    for(int i=0;i<N;i++){
        if(std::isnan(sma20[i])) continue;
        double x=xmap(i), y=ymap(sma20[i]);
        if(first){ cairo_move_to(cr,x,y); first=false; } else cairo_line_to(cr,x,y);
    }
    cairo_stroke(cr);

    // close
    cairo_set_source_rgb(cr,0.9,0.9,0.9);
    cairo_set_line_width(cr,1.5);
    first=true;
    for(int i=0;i<N;i++){
        double x=xmap(i), y=ymap(S.close[i]);
        if(first){ cairo_move_to(cr,x,y); first=false; } else cairo_line_to(cr,x,y);
    }
    cairo_stroke(cr);

    draw_text(W-320,20,"Precio (blanco)  SMA20 (amarillo)",0.9,0.9,0.9);
    draw_text(W-320,38,"Bandas Bollinger (azul)",0.6,0.8,1.0);

    cairo_surface_write_to_png(surf, outpng.c_str());
    cairo_destroy(cr);
    cairo_surface_destroy(surf);
    return true;
}

// =================== HTML ===================
static bool write_html(const std::string& path, const std::string& symbol,
                       int pred_s, int ind_s, int news_s, int final_s,
                       const std::string& png_name, const Series& S)
{
    std::ofstream o(path); if(!o) return false;
    o<<"<!doctype html><html><head><meta charset='utf-8'>"
     <<"<title>Informe "<<symbol<<"</title>"
     <<"<style>body{font-family:Arial,Helvetica,sans-serif;background:#0f131a;color:#eaeaea;padding:20px}"
     <<"h1,h2{color:#9cd1ff} table{border-collapse:collapse;width:100%;margin-top:10px}"
     <<"th,td{border:1px solid #2a2e36;padding:6px;text-align:left}</style></head><body>";
    o<<"<h1>Informe "<<symbol<<"</h1>";
    o<<"<p><b>Fecha/Hora:</b> "<<now_iso()<<"</p>";
    o<<"<h2>Puntuaciones</h2><ul>";
    o<<"<li><b>Predicción:</b> "<<pred_s<<"</li>";
    o<<"<li><b>Indicadores:</b> "<<ind_s<<"</li>";
    o<<"<li><b>Noticias:</b> "<<news_s<<"</li>";
    o<<"<li><b>Final:</b> "<<final_s<<"</li>";
    o<<"</ul>";
    o<<"<h2>Indicadores (gráfico)</h2>";
    o<<"<img src='"<<png_name<<"' alt='indicadores' style='max-width:100%;border:1px solid #2a2e36;border-radius:8px'/>";

    // últimas 10 filas
    o<<"<h2>Últimas velas</h2><table><tr><th>time_utc</th><th>open</th><th>high</th><th>low</th><th>close</th></tr>";
    int N=S.close.size();
    int start=std::max(0,N-10);
    for(int i=start;i<N;i++){
        o<<"<tr><td>"<<S.t[i]<<"</td><td>"<<S.open[i]<<"</td><td>"<<S.high[i]<<"</td><td>"<<S.low[i]<<"</td><td>"<<S.close[i]<<"</td></tr>";
    }
    o<<"</table></body></html>";
    return true;
}

// =================== MAIN ===================
int main(int argc, char** argv){
    // Uso: fx_report_all candles.csv epochs batch T H thresh_bp [newsString]
    if(argc < 7){
        std::cerr<<"Uso: "<<argv[0]<<" candles.csv epochs batch T H thresh_bp [10100 | \"1,0,1,0,0\"]\n";
        return 1;
    }
    std::string csv   = argv[1];
    int epochs        = std::atoi(argv[2]);
    int batch         = std::atoi(argv[3]);
    int T             = std::atoi(argv[4]);
    int H             = std::atoi(argv[5]);
    int thresh_bp     = std::atoi(argv[6]); // umbral en basis points

    if(T<=0||H<=0){ std::cerr<<"T y H deben ser >0\n"; return 2; }

    // Noticias: si el ÚLTIMO arg es cadena válida, úsala; si no, pedir por stdin
    int n1=0,n2=0,n3=0,n4=0,n5=0;
    bool took_cli = (argc >= 8) && parse_news_from_last_arg(argc, argv, n1,n2,n3,n4,n5);
    if(!took_cli){
        std::cout<<"Noticias... ingrese 5 valores 0/1 (ej. 1 0 1 0 0): ";
        if(!(std::cin>>n1>>n2>>n3>>n4>>n5)){ n1=n2=n3=n4=n5=0; }
    }
    int news_raw = n1+n2+n3+n4+n5; // 0..5
    int news_score = (news_raw==0?-2 : news_raw==1?-1 : news_raw<=3?0 : news_raw==4?+1 : +2);

    // Leer CSV
    Series S;
    if(!read_csv(csv, S)){ return 3; }
    int N=S.close.size();
    if(N < T+H){
        std::cerr<<"Pocos datos para T/H. N="<<N<<" T="<<T<<" H="<<H<<"\n";
        return 4;
    }

    // Indicadores técnicos
    auto rsi14 = RSI(S.close, 14);
    std::vector<double> M, MS, MH;
    MACD(S.close, 12,26,9, M,MS,MH);
    auto sma20 = SMA(S.close,20);
    auto sma50 = SMA(S.close,50);
    std::vector<double> bbM, bbU, bbL;
    Boll(S.close, bbM, bbU, bbL, 20, 2.0);

    int votes=0, total=0;
    if(!std::isnan(rsi14.back())){ total++; if(rsi14.back()<30) votes++; else if(rsi14.back()>70) votes--; }
    if(!std::isnan(M.back()) && !std::isnan(MS.back())){ total++; if(M.back()>MS.back()) votes++; else votes--; }
    if(!std::isnan(sma20.back()) && !std::isnan(sma50.back())){ total++; if(sma20.back()>sma50.back()) votes++; else votes--; }
    if(!std::isnan(bbM.back())){ total++; if(S.close.back()>bbM.back()) votes++; else votes--; }
    double ratio = (total? (double)votes/total : 0.0);
    int ind_score = (ratio>=0.6? +2 : ratio>=0.2? +1 : ratio<=-0.6? -2 : ratio<=-0.2? -1 : 0);

    // Ventanas GRU
    torch::Tensor X,Y; double mu=0,sd=1;
    make_windows(S.close, T, H, X, Y, mu, sd);
    if(!X.defined() || X.size(0)==0){
        std::cerr<<"Pocos datos tras ventanas.\n"; return 5;
    }

    // Entrena GRU
    GRUModel model(1,32,1,1);
    model->to(torch::kCPU);
    torch::optim::Adam opt(model->parameters(), torch::optim::AdamOptions(1e-3));
    int Msz=X.size(0);
    int bs=std::max(1, std::min(batch, Msz));
    for(int e=0;e<epochs;e++){
        model->train();
        for(int i=0;i<Msz;i+=bs){
            int b=std::min(bs, Msz-i);
            auto xb=X.index({torch::indexing::Slice(i,i+b)});
            auto yb=Y.index({torch::indexing::Slice(i,i+b)});
            auto pred=model->forward(xb);
            auto loss=torch::mse_loss(pred,yb);
            opt.zero_grad(); loss.backward(); opt.step();
        }
    }

    // Predicción última ventana
    auto last_in = X.index({-1}).unsqueeze(0); // [1,T,1]
    model->eval();
    auto yhat_n = model->forward(last_in);
    double yhat = yhat_n[0][0].item<double>()*sd + mu;
    double last = S.close.back();
    double delta = yhat - last;
    double delta_bp = (delta/last)*10000.0;

    int thr = std::max(1, thresh_bp);
    int pred_score = (delta_bp >= 2*thr? +2 :
                      delta_bp >=    thr? +1 :
                      delta_bp <= -2*thr? -2 :
                      delta_bp <=   -thr? -1 : 0);

    // Puntaje final (promedio redondeado y limitado)
    int final_score = std::max(-2, std::min(2, (int)std::llround((pred_score + ind_score + news_score)/3.0)));

    // Salidas
    std::string png_name="indicadores.png";
    std::string html_name="informe.html";
    draw_png(png_name, "SYMBOL", S, sma20, bbU, bbL, pred_score, ind_score, news_score, final_score);
    write_html(html_name, "SYMBOL", pred_score, ind_score, news_score, final_score, png_name, S);

    std::cout<<"OK. pred="<<pred_score<<" ind="<<ind_score<<" news="<<news_score<<" final="<<final_score<<"\n";
    std::cout<<"Generados: "<<png_name<<"  "<<html_name<<"\n";
    return 0;
}
