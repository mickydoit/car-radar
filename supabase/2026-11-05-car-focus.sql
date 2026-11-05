-- Car Radar shares watch-radar's Supabase project and password.
-- Adds the 'car_focus' setting id so the car site can save its daily search scope.
-- Apply with:
--   supabase db query --linked --project-ref xyilyuhwyrgxkcfbmayz -f supabase/2026-11-05-car-focus.sql
create or replace function public.watch_radar_save(p_password text, p_id text, p_data jsonb)
returns jsonb language plpgsql security definer set search_path = public, extensions as $$
begin
  if not watch_radar_check(p_password) then raise exception 'wrong password' using errcode = '28000'; end if;
  if p_id not in ('focus', 'watchlist', 'marks', 'car_focus') then raise exception 'unknown setting %', p_id; end if;
  insert into watch_radar_settings (id, data, updated_at) values (p_id, p_data, now())
  on conflict (id) do update set data = excluded.data, updated_at = now();
  return jsonb_build_object('ok', true, 'id', p_id, 'updated_at', now());
end $$;
