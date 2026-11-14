icalendar.expansion package
==========================

Event expansion turns the recurrence definitions stored in a calendar into the
concrete :class:`~icalendar.Occurrence` records happening inside a query
window. The public entry point is
:meth:`icalendar.Calendar.expand_events`.

.. automodule:: icalendar.expansion
   :members: Occurrence, expand_events
   :show-inheritance:

.. automodule:: icalendar.expansion.occurrence
   :members:
   :show-inheritance:

Submodules
----------

.. toctree::
   :maxdepth: 4

   icalendar.expansion.engine
   icalendar.expansion.etl
   icalendar.expansion.model
   icalendar.expansion.rrule
   icalendar.expansion.time
