# ARIA — IA de póker que aprende de sus rivales (Hold'em No-Limit, 3 jugadores)

Cada jugador recibe 2 cartas privadas y hay 5 comunitarias. Ciegas 1/2, stacks de 100 ciegas grandes.
Solo Python estándar para el juego y la IA; `matplotlib` y `numpy` solo para las gráficas.

## Uso

```bash
python test_engine.py        # verifica evaluador de manos y reglas (fichas conservadas, botes laterales)
python run.py demo           # partida en vivo: lo que ARIA lee de sus rivales vs la realidad
python run.py experiments    # ARIA vs gemelo congelado, mismas cartas, 4 mesas x 4 semillas (~9 min, 2 núcleos)
python run.py shift          # los rivales cambian de estilo a mitad de partida
python run.py plots          # gráficas PNG
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `engine.py` | Reglas, evaluador de 5-7 cartas, apuestas, all-ins y botes laterales |
| `equity.py` | Fuerza de mano: tabla preflop (Monte Carlo, se guarda en `preflop_cache.json`) y percentiles posflop |
| `bots.py` | Rivales con estilos distintos: Roca, Estación de llamadas, Maníaco, TAG |
| `learner.py` | **ARIA**: modelo de rivales + decisión por valor esperado |
| `stats.py` | Estadísticas reales de cada asiento, medidas desde fuera, para comparar con lo que ARIA cree |
| `run.py`, `plots.py` | Demostración, experimentos y gráficas |

## Cómo aprende ARIA

1. **Frecuencias por contexto** (calle, ¿enfrenta apuesta?, tamaño): probabilidad de fold / igualar / subir de cada rival.
   Priors bayesianos, jerarquía entre calles y olvido exponencial para notar cambios de estilo.
2. **Rangos**: la acción de un rival implica una franja de la fuerza de su mano, corregida con los showdowns
   reales (histograma de qué tenía cuando apostó), lo que captura sus faroles.
3. **Decisión**: simula 300 manos posibles de los rivales y elige la acción de mayor valor esperado, modelando
   cómo responderían (fold / call / re-subida). Los faroles y los value bets finos salen del cálculo; no hay reglas por tipo de rival.

## Por qué la evidencia es válida

- **Control**: el gemelo congelado usa el mismo código y priors, pero no actualiza nada. Ambos juegan las mismas cartas
  (el reparto depende solo de semilla y número de mano), así que la diferencia se debe al aprendizaje.
- **Sin ventajas ocultas**: ARIA solo recibe las acciones públicas y las cartas reveladas en showdown.
- **Lectura medida**: `stats.py` mide la verdad desde fuera y se compara con la estimación de ARIA.
- **Cambio de régimen**: tras cambiar el estilo de los rivales, ARIA con olvido se readapta; sin olvido o congelada, no.

## Límites honestos

- Los rivales son bots paramétricos, mucho más explotables que humanos; los bb/100 absolutos no son realistas.
  Lo que importa es la **diferencia** entre ARIA y su gemelo congelado.
- Cada decisión usa una simulación de una sola calle con realización de equity fija; no es un solver de equilibrio.
- Un all-in corto no reabre la acción. Los stacks se reponen en cada mano.
- Los showdowns solo muestran manos que no se retiraron, así que los faroles se subestiman un poco.
