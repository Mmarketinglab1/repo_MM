from database import engine
import models

print("Creando nuevas tablas...")
models.Base.metadata.create_all(bind=engine)
print("¡Tablas creadas exitosamente!")
