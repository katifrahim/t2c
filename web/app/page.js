import Workspace from "../components/Workspace";
import Landing from "../components/Landing";
import { createClient } from "@/lib/supabase/server";

// Logged-out visitors see the landing page; signed-in users get the app
// (3D viewer + chat). When Supabase isn't configured (local dev), skip the
// check and go straight to the app.
export default async function Page() {
  if (process.env.NEXT_PUBLIC_SUPABASE_URL) {
    const supabase = await createClient();
    const { data } = await supabase.auth.getClaims();
    if (!data?.claims) return <Landing />;
  }
  return <Workspace />;
}
