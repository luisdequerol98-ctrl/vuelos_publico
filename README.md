# Buscador de vuelos baratos

Aplicación local que vigila el precio de un viaje de ida y vuelta, te avisa por
Telegram cuando baja de tu precio objetivo y te propone alternativas más baratas.

## Cómo se usa

1. Doble clic en `arrancar.bat`. Se abre `http://127.0.0.1:5077`.
2. **Ajustes → Telegram**: crea un bot con @BotFather, pega el token, escríbele «hola»,
   pulsa *Detectar mi chat* y *Enviar prueba*.
3. Crea una alerta: origen, destino, primer día de ida, último día de vuelta,
   días en destino (± flexibilidad), precio objetivo, máximo de horas por trayecto
   (opcional) y si solo quieres directos.

**Origen y destino** se buscan escribiendo una ciudad («Lisboa», también en inglés), una isla
o región («Creta», «Canarias»), un país («Japón») o el código («LIS»). La lista tiene los
4.028 aeropuertos con vuelos regulares de OurAirports (`static/aeropuertos.json`); se
regenera con `herramientas/generar_aeropuertos.py`. Si una ciudad tiene varios aeropuertos
(Londres, París, Milán…), hay que elegir uno de la lista.

El **límite de horas** se aplica a la ida y a la vuelta por separado y cuenta las esperas
en las escalas. Si quitarlo sale más barato, la página lo propone como alternativa
(«Aceptar trayectos de hasta 30 h: −51 €»).

Las revisiones se hacen solas **5 veces al día a horas aleatorias** (se sortean cada
medianoche, separadas al menos 2 h). Solo se hacen si el ordenador está encendido y
la aplicación abierta. Si a una hora sorteada el equipo estaba apagado o suspendido
más de 45 minutos, esa revisión se marca como perdida.

## Revisar con el PC apagado (GitHub Actions)

En **Ajustes → Revisar con el PC apagado** se conecta con un repositorio **privado** de
GitHub. A partir de ahí:

- GitHub Actions revisa los precios aunque el PC esté apagado (gratis: 2.000 minutos al
  mes en repositorios privados). Medido el 17/11/2026: Google responde de forma irregular
  desde GitHub (falla ~1 de cada 2 primeros intentos) y cada consulta se repite hasta 3
  veces; así se recupera casi todo (2 fallos de 28), pero cada revisión gasta de 2 a 10
  minutos por alerta según el rango. Con 5 revisiones al día caben 1 o 2 alertas con rangos
  de un mes.
- Cada día se eligen al azar 5 de 8 huecos repartidos por el día (el número se cambia en
  Ajustes, máximo 8). GitHub suele arrancar con unos minutos de retraso, lo que añade azar.
- Tus alertas viven en `alertas.json` (rama principal) y los resultados en `vuelos.db`
  (rama `datos`). Los tokens de Telegram y Aviasales van como **secretos** del repositorio.
- Esta página sigue funcionando igual: enseña lo que ha encontrado GitHub y, al crear o
  cambiar una alerta, la guarda allí (la primera revisión empieza sola en 1-2 minutos).
- Si cambias el código, «Subir el código otra vez» lo actualiza en GitHub.
- «Volver al modo local» hace que vuelva a revisar este PC. Para que GitHub deje de
  revisar, desactiva el flujo «Revisar vuelos» en la pestaña *Actions* del repositorio.

Si alguna consulta a Google se pierde, aparece en «Incidencias de la última revisión».
Ryanair y Wizz Air funcionan desde GitHub sin problemas.

Token necesario (*fine-grained*, solo para ese repositorio): Actions, Contents, Secrets y
Workflows en *Read and write*.

**Repositorio público** (minutos de GitHub ilimitados y gratis): la app lo acepta si lo
confirmas al conectar. Quedan a la vista tus alertas, el historial de precios y los registros
de cada revisión; los tokens siguen ocultos como secretos. Los commits que hace la app usan
el correo anónimo de GitHub (`…@users.noreply.github.com`), pero los commits anteriores al
17/11/2026 tienen el correo de la cuenta: para un repositorio público, mejor uno nuevo.
GitHub desactiva los flujos programados de un repositorio público tras 60 días sin actividad
(avisa por correo y se reactiva con un clic).

**Redes de empresa**: si la red inspecciona HTTPS (Netskope, Zscaler…), la app usa los
certificados de Windows gracias a `truststore`; sin eso, GitHub falla con
`CERTIFICATE_VERIFY_FAILED`.

## Qué consulta

| Fuente | Qué aporta | Coste |
|---|---|---|
| Google Flights | Casi todas las aerolíneas y agencias, solo ida por día + ida y vuelta en las 12 mejores combinaciones | Gratis, sin clave |
| Ryanair | Precio más barato de cada día (Google Flights no muestra Ryanair) | Gratis, sin clave |
| Wizz Air | Precio más barato de cada día (Google Flights tampoco muestra Wizz Air). Cobra en la moneda del aeropuerto de salida: se pasa a euros con el cambio del BCE del día | Gratis, sin clave |
| Aviasales (opcional) | Precios vistos por otros usuarios en las últimas 48 h | Token gratuito de travelpayouts.com |

Duraciones: Google Flights y Ryanair dan horas reales (con zonas horarias). Wizz Air no
da hora de llegada, así que su duración se estima por distancia (se marca con «≈»).

En cada revisión se mira tu rango **± 5 días** y tu duración **± 2 días**, para poder
proponer alternativas. Cada combinación se queda con lo más barato entre dos billetes
de solo ida (pueden ser de webs distintas) y un billete de ida y vuelta.

## Qué enseña la página

- Mejor precio en tus fechas frente a tu objetivo, mejor precio hasta ahora y número de revisiones.
- **Historial de precios**: el mejor precio hasta ahora (qué vuelo era, cuándo salió y cuánto
  cuesta hoy esa misma combinación), el gráfico por revisión con el mínimo marcado, en total y
  por web, y la tabla de todas las revisiones con el cambio frente a la anterior.
- **Calendario de precios**: día de salida × días en destino.
- **Cómo pagar menos**: otra duración, salir antes o volver después, aceptar escalas,
  el día de la semana más barato y, cuando hay datos, la franja horaria de búsqueda más barata.
- **¿Influye la hora de búsqueda?**: cada vuelo se compara con su propio precio medio
  de ese día, por franjas (madrugada, mañana, tarde, noche). Necesita unos días de datos.
- Las 10 combinaciones más baratas y el registro de avisos enviados.

## Avisos

Se envía un aviso cuando el mejor precio en tus fechas es igual o inferior a tu
objetivo. No se repite mientras no baje más; si vuelve a subir por encima, el siguiente
cruce vuelve a avisar.

## Archivos

- `app.py`: servidor web y API · `motor.py`: revisiones, combinaciones, alternativas y análisis horario
- `fuentes.py`: Google Flights, Ryanair y Aviasales · `planificador.py`: las 5 horas aleatorias
- `avisos.py`: Telegram · `db.py`: base de datos `vuelos.db` · `static/`: la página
- `static/aeropuertos.json` y `herramientas/generar_aeropuertos.py`: el buscador de aeropuertos
- `buscador.log`: registro de cada revisión y de los errores

## Límites conocidos

- Precios por persona adulta, en euros, clase turista.
- Google Flights, Ryanair y Wizz Air no tienen API oficial: si cambian su web, la fuente
  puede dejar de responder. La página lo muestra en «Incidencias de la última revisión».
- Equipaje facturado y tasas de pago con tarjeta no incluidos (sobre todo en low cost).
