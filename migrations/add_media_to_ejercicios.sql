-- ============================================================
-- Migración: Agregar soporte de media a la tabla ejercicios
-- Aplicar manualmente: psql -U <user> -d <db> -f este_archivo.sql
-- ============================================================

ALTER TABLE ejercicios
  ADD COLUMN IF NOT EXISTS imagenes JSONB,
  ADD COLUMN IF NOT EXISTS video    TEXT;

COMMENT ON COLUMN ejercicios.imagenes IS 'Lista de hasta 3 imágenes en base64 JPEG comprimidas';
COMMENT ON COLUMN ejercicios.video    IS 'Video demostrativo en base64 (WebM/MP4, máx ~1 MB tras compresión)';
