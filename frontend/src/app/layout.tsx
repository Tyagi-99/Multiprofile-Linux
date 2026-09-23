import type { Metadata } from "next";
import "../styles/tokens.css";

export const metadata: Metadata = {
  title: "Multiprofile Linux — Operations Console",
  description: "Premium browser operations console for Linux.",
  icons: { icon: "/favicon.ico" },
};

/**
 * Pre-paint theme bootstrap. Runs before CSS so the window never flashes
 * the wrong theme. Mirrors the current console's contract:
 * localStorage["mp_theme"] wins, otherwise prefers-color-scheme.
 * pywebview may run without localStorage — everything is try/catch.
 */
const THEME_BOOTSTRAP = `(function(){try{var t=null;try{t=window.localStorage.getItem("mp_theme")}catch(e){}if(t!=="light"&&t!=="dark"){t=(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches)?"dark":"light"}document.documentElement.setAttribute("data-theme",t)}catch(e){document.documentElement.setAttribute("data-theme","light")}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
